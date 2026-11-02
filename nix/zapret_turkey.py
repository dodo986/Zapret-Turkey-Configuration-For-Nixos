#!/usr/bin/env python3
"""Install and manage the Zapret Turkey module on NixOS.

This helper is deliberately standard-library only. It is packaged by flake.nix
so `nix run` works on a fresh NixOS install without adding Python packages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

__version__ = "2.0.0"

SHARE_DIR = Path(__file__).resolve().parent
MODULE_SOURCE = SHARE_DIR / "module.nix"
PRESETS_SOURCE = SHARE_DIR / "presets.json"

DEFAULT_CONFIG = Path("/etc/nixos/configuration.nix")
DEFAULT_SETTINGS = Path("/etc/nixos/zapret-turkey.nix")
DEFAULT_MODULE = Path("/etc/nixos/zapret-turkey-module.nix")

IMPORT_LINE = "./zapret-turkey.nix"
DEFAULT_PRESETS = ["discord", "roblox"]
DEFAULT_DNS = ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4"]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def die(message: str, code: int = 1) -> "NoReturn":
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def info(message: str = "") -> None:
    print(message)


def load_presets() -> dict[str, list[str]]:
    try:
        with PRESETS_SOURCE.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        die(f"cannot read {PRESETS_SOURCE}: {exc}")
    except json.JSONDecodeError as exc:
        die(f"{PRESETS_SOURCE} is not valid JSON: {exc}")
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, list) and all(isinstance(x, str) for x in value)
        for key, value in data.items()
    ):
        die(f"{PRESETS_SOURCE} has an unexpected shape")
    return data


def split_csv(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        for part in value.replace(";", ",").split(","):
            part = part.strip()
            if part:
                result.append(part)
    return result


def normalize_domain(raw: str) -> str:
    """Turn a URL, wildcard or hostname into a zapret hostlist entry."""
    value = raw.strip().lower()
    for scheme in ("https://", "http://", "ftp://"):
        if value.startswith(scheme):
            value = value[len(scheme) :]
            break
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    value = re.split(r"[#?/]", value, maxsplit=1)[0]
    if ":" in value:
        value = value.split(":", 1)[0]
    value = value.lstrip(".")
    if value.startswith("*."):
        value = value[2:]
    if value.startswith("www."):
        value = value[4:]
    value = value.rstrip(".")
    if not value or any(ch.isspace() for ch in value):
        die(f"invalid domain: {raw!r}")
    return value


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def is_excluded(domain: str, excludes: list[str]) -> bool:
    return any(domain == item or domain.endswith("." + item) for item in excludes)


def expand_domains(
    presets_data: dict[str, list[str]],
    preset_names: list[str],
    extra_domains: list[str],
    excludes: list[str],
    bypass_all: bool,
) -> tuple[list[str], list[str]]:
    unknown = [name for name in preset_names if name not in presets_data]
    if unknown:
        die(
            "unknown preset(s): "
            + ", ".join(unknown)
            + "\nAvailable presets: "
            + ", ".join(sorted(presets_data))
        )

    domains: list[str] = []
    for name in preset_names:
        domains.extend(presets_data[name])
    domains.extend(extra_domains)

    normalized = unique([normalize_domain(item) for item in domains])
    normalized_excludes = unique([normalize_domain(item) for item in excludes])

    if bypass_all:
        return normalized, normalized_excludes

    filtered = [domain for domain in normalized if not is_excluded(domain, normalized_excludes)]
    return filtered, normalized_excludes


def nix_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")


def nix_list(items: list[str], indent: str = "    ") -> str:
    if not items:
        return "[ ]"
    joined = " ".join(f'"{nix_escape(item)}"' for item in items)
    if len(indent) + len(joined) + 6 <= 100:
        return f"[ {joined} ]"
    lines = [f'{indent}  "{nix_escape(item)}"' for item in items]
    return "[\n" + "\n".join(lines) + f"\n{indent}]"


def backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak.{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak.{stamp}.{counter}")
        counter += 1
    return candidate


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = backup_path(path)
    shutil.copy2(path, backup)
    return backup


def write_text(path: Path, text: str) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_file(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    return backup


def run_command(command: list[str], *, dry_run: bool = False) -> None:
    suffix = " (dry run)" if dry_run else ""
    info("$ " + " ".join(command) + suffix)
    if dry_run:
        return
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError:
        die(f"command not found: {command[0]}")
    except subprocess.CalledProcessError as exc:
        die(f"command failed with exit code {exc.returncode}: {' '.join(command)}")


def run_capture(command: list[str], timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"(unavailable: {exc})"
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0 and not output:
        return error or f"(exit code {result.returncode})"
    return output


def find_nixos_rebuild() -> str:
    candidate = shutil.which("nixos-rebuild")
    if candidate:
        return candidate
    fallback = Path("/run/current-system/sw/bin/nixos-rebuild")
    if fallback.exists():
        return str(fallback)
    die("nixos-rebuild was not found. Are you running this on NixOS?")


def rebuild(flake_attr: str | None, dry_run: bool) -> None:
    command = [find_nixos_rebuild(), "switch"]
    flake = Path("/etc/nixos/flake.nix")
    if flake.exists():
        reference = "/etc/nixos"
        if flake_attr:
            reference += "#" + flake_attr
        command.extend(["--flake", reference])
        info("Detected /etc/nixos/flake.nix; using flake rebuild.")
    run_command(command, dry_run=dry_run)


def require_root(args: argparse.Namespace) -> None:
    if getattr(args, "dry_run", False) or os.geteuid() == 0:
        return
    sudo = shutil.which("sudo")
    if not sudo:
        die("this command needs root. Re-run it as root or install sudo.")
    info("Root is required; re-running with sudo...")
    os.execvp(
        sudo,
        [sudo, sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
    )


# ---------------------------------------------------------------------------
# generated Nix files
# ---------------------------------------------------------------------------


def render_module() -> str:
    """Inline presets.json so /etc/nixos stays self-contained."""
    try:
        module = MODULE_SOURCE.read_text(encoding="utf-8")
        presets = json.loads(PRESETS_SOURCE.read_text(encoding="utf-8"))
    except OSError as exc:
        die(f"cannot read packaged Nix sources: {exc}")
    except json.JSONDecodeError as exc:
        die(f"packaged presets.json is invalid: {exc}")

    marker = "builtins.fromJSON (builtins.readFile ./presets.json)"
    if module.count(marker) != 1:
        die("packaged module.nix changed unexpectedly; cannot inline presets")
    json_text = json.dumps(presets, indent=2, ensure_ascii=False)
    indented = "\n".join("  " + line for line in json_text.splitlines())
    embedded = "builtins.fromJSON ''\n" + indented + "\n  ''"
    return module.replace(marker, embedded, 1)


def render_settings(
    *,
    presets: list[str],
    extra_domains: list[str],
    excludes: list[str],
    bypass_all: bool,
    strategy: str,
    ttl: int,
    autottl: int,
    fooling: str,
    repeats: int,
    extra_params: list[str],
    secure_dns: bool,
    dns_servers: list[str],
    dnssec: bool,
    http_support: bool,
    udp_support: bool,
    manage_firewall: bool,
    qnum: int,
) -> str:
    lines = [
        f"# Generated by zapret-turkey {__version__} on {datetime.now():%Y-%m-%d %H:%M:%S}.",
        "# Edit this file, then run: sudo nixos-rebuild switch",
        "{ ... }:",
        "{",
        "  imports = [ ./zapret-turkey-module.nix ];",
        "",
        "  services.zapret-turkey = {",
        "    enable = true;",
        "",
        "    # Presets expand to maintained domain lists. Subdomains are included",
        "    # automatically by zapret, so there is no need to list every URL.",
        f"    presets = {nix_list(presets)};",
        f"    extraDomains = {nix_list(extra_domains)};",
    ]
    if bypass_all:
        lines.append("    # Ignore the whitelist and bypass every domain.")
        lines.append("    bypassAll = true;")
    if excludes:
        lines.append(f"    excludeDomains = {nix_list(excludes)};")
    lines.extend(
        [
            "",
            "    # Packet strategy. Tune ttl first if sites still fail.",
            f'    strategy = "{strategy}";',
            f"    ttl = {ttl};",
            f"    autottl = {autottl};",
            f'    fooling = "{nix_escape(fooling)}";',
            f"    repeats = {repeats};",
        ]
    )
    if extra_params:
        lines.append(f"    extraParams = {nix_list(extra_params)};")
    lines.append("")
    if not secure_dns:
        lines.append("    secureDns = false;")
    else:
        lines.append("    secureDns = true;")
        if dns_servers:
            lines.append(f"    dnsServers = {nix_list(dns_servers)};")
        if dnssec:
            lines.append("    dnssec = true;")
    if not http_support:
        lines.append("    httpSupport = false;")
    if not udp_support:
        lines.append("    udpSupport = false;")
    if not manage_firewall:
        lines.append("    manageFirewall = false;")
    if qnum != 200:
        lines.append(f"    qnum = {qnum};")
    lines.extend(
        [
            "  };",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# configuration.nix editing
# ---------------------------------------------------------------------------


def add_import(config_path: Path) -> Path | None:
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        die(f"cannot read {config_path}: {exc}")

    if IMPORT_LINE in text:
        info(f"{config_path} already imports {IMPORT_LINE}; leaving it alone.")
        return None

    match = re.search(r"(?m)^([ \t]*)imports[ \t]*=[ \t]*\[", text)
    if match:
        indent = match.group(1)
        start = match.end()
        depth = 1
        index = start
        while index < len(text):
            if text[index] == "[":
                depth += 1
            elif text[index] == "]":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if index >= len(text):
            die(f"cannot find the end of the imports list in {config_path}")

        body = text[start:index]
        if "\n" in body:
            # Multiline list: add one entry at the top and keep comments intact.
            insertion = "\n" + indent + "  " + IMPORT_LINE
            text = text[:start] + insertion + text[start:]
        else:
            # Inline list: re-render it one item per line.
            items = [item for item in body.split() if item]
            items.insert(0, IMPORT_LINE)
            new_body = (
                "\n"
                + "\n".join(indent + "  " + item for item in items)
                + "\n"
                + indent
            )
            text = text[:start] + new_body + text[index:]
    else:
        index = text.rfind("}")
        if index == -1:
            die(f"cannot find the top-level attrset in {config_path}")
        insertion = f"\n  imports = [ {IMPORT_LINE} ];\n"
        text = text[:index] + insertion + text[index:]

    backup = write_text(config_path, text)
    info(f"Added {IMPORT_LINE} to {config_path}.")
    return backup


def remove_import(config_path: Path) -> Path | None:
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        die(f"cannot read {config_path}: {exc}")

    if IMPORT_LINE not in text:
        info(f"{config_path} does not import {IMPORT_LINE}; nothing to remove.")
        return None

    text = re.sub(r"(?m)^[ \t]*" + re.escape(IMPORT_LINE) + r"[ \t]*\n", "", text)
    text = text.replace(IMPORT_LINE, "")
    backup = write_text(config_path, text)
    info(f"Removed {IMPORT_LINE} from {config_path}.")
    return backup


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def collect_presets(
    args: argparse.Namespace, presets_data: dict[str, list[str]]
) -> tuple[list[str], bool]:
    raw = split_csv(args.presets)
    raw.extend(args.preset)
    explicit = bool(args.presets or args.preset)
    bypass_all = bool(getattr(args, "all", False))

    # `--presets all` selects every maintained preset. `--all` is the
    # separate "do not use a whitelist at all" mode.
    if "all" in raw:
        raw = [name for name in raw if name != "all"] + sorted(presets_data)

    if not raw and not explicit and not bypass_all:
        raw = list(DEFAULT_PRESETS)

    return unique(raw), bypass_all


def cmd_install(args: argparse.Namespace) -> int:
    require_root(args)

    if args.strategy == "custom" and not args.params:
        die("--strategy custom requires at least one --param=FLAG")

    presets_data = load_presets()
    preset_names, bypass_all = collect_presets(args, presets_data)
    extra_domains = split_csv(args.domains) + split_csv(args.domain_lists)
    excludes = split_csv(args.exclude)

    domains, normalized_excludes = expand_domains(
        presets_data,
        preset_names,
        extra_domains,
        excludes,
        bypass_all,
    )

    dns_servers = split_csv(args.dns) if args.dns else DEFAULT_DNS
    # With --all the whitelist is irrelevant, so do not leave a stale preset
    # list behind if the user later turns bypassAll off.
    settings_text = render_settings(
        presets=[] if bypass_all else preset_names,
        extra_domains=[] if bypass_all else unique([normalize_domain(item) for item in extra_domains]),
        excludes=normalized_excludes,
        bypass_all=bypass_all,
        strategy=args.strategy,
        ttl=args.ttl,
        autottl=args.autottl,
        fooling=args.fooling,
        repeats=args.repeats,
        extra_params=args.params,
        secure_dns=not args.no_dns,
        dns_servers=dns_servers,
        dnssec=args.dnssec,
        http_support=not args.no_http,
        udp_support=not args.no_udp,
        manage_firewall=not args.no_firewall,
        qnum=args.qnum,
    )
    module_text = render_module()

    info()
    info("Zapret Turkey configuration")
    info("---------------------------")
    if bypass_all:
        info("mode:            bypass all domains")
    else:
        info(f"presets:         {', '.join(preset_names) if preset_names else '(none)'}")
        info(f"extra domains:   {len(extra_domains)}")
        info(f"effective:       {len(domains)} domains")
        if normalized_excludes:
            info(f"excluded:        {', '.join(normalized_excludes)}")
    info(f"strategy:        {args.strategy} (ttl={args.ttl}, autottl={args.autottl})")
    info(f"secure DNS:      {'yes' if not args.no_dns else 'no'}")
    info(f"firewall rules:  {'managed' if not args.no_firewall else 'left alone'}")
    info()

    if args.dry_run:
        info(f"Would write {args.settings_file}:")
        info()
        info(settings_text)
        info(f"Would write {args.module_file} (packaged NixOS module with inlined presets).")
        info(f"Would add {IMPORT_LINE} to {args.config}.")
        if not args.no_rebuild:
            info("Would run nixos-rebuild switch.")
        return 0

    if not args.config.exists():
        die(f"{args.config} was not found. Pass --config /path/to/configuration.nix.")

    if (args.settings_file.exists() or args.module_file.exists()) and not args.yes:
        try:
            answer = input("Existing zapret-turkey files found. Overwrite them? [y/N] ")
        except EOFError:
            die("existing files found; pass --yes to overwrite")
        if answer.strip().lower() not in {"y", "yes"}:
            die("aborted; nothing was changed")

    settings_backup = write_text(args.settings_file, settings_text)
    module_backup = write_text(args.module_file, module_text)
    import_backup = add_import(args.config)

    if settings_backup:
        info(f"Backup: {settings_backup}")
    if module_backup:
        info(f"Backup: {module_backup}")
    if import_backup:
        info(f"Backup: {import_backup}")

    if not bypass_all and not domains:
        info()
        info("warning: no domains selected. Zapret will bypass every domain.")
        info("Add --presets, --domain, or use --all to make this explicit.")

    info()
    info(f"Wrote {args.settings_file}")
    info(f"Wrote {args.module_file}")

    if args.no_rebuild:
        info("Skipping rebuild. Apply with: sudo nixos-rebuild switch")
    else:
        info()
        info("Rebuilding NixOS...")
        rebuild(args.flake_attr, dry_run=False)
        info()
        info("Done. Check it with: zapret-turkey status")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    require_root(args)

    if args.dry_run:
        info(f"Would remove {IMPORT_LINE} from {args.config}.")
        info(f"Would back up and remove {args.settings_file} and {args.module_file}.")
        if not args.no_rebuild:
            info("Would run nixos-rebuild switch.")
        return 0

    import_backup = remove_import(args.config)
    if import_backup:
        info(f"Backup: {import_backup}")

    for path in (args.settings_file, args.module_file):
        if path.exists():
            backup = backup_path(path)
            path.replace(backup)
            info(f"Moved {path} to {backup}")

    if args.no_rebuild:
        info("Skipping rebuild. Apply with: sudo nixos-rebuild switch")
    else:
        info()
        info("Rebuilding NixOS...")
        rebuild(args.flake_attr, dry_run=False)
        info("Zapret Turkey removed.")
    return 0


def cmd_domains(args: argparse.Namespace) -> int:
    presets_data = load_presets()
    preset_names, bypass_all = collect_presets(args, presets_data)
    extra_domains = split_csv(args.domains) + split_csv(args.domain_lists)
    excludes = split_csv(args.exclude)
    domains, normalized_excludes = expand_domains(
        presets_data, preset_names, extra_domains, excludes, bypass_all
    )

    if args.json:
        print(
            json.dumps(
                {
                    "bypassAll": bypass_all,
                    "presets": preset_names,
                    "domains": domains,
                    "excludes": normalized_excludes,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    elif bypass_all:
        print("*")
    else:
        for domain in domains:
            print(domain)
    return 0


def cmd_list_presets(args: argparse.Namespace) -> int:
    presets_data = load_presets()
    if args.json:
        print(json.dumps(presets_data, indent=2, ensure_ascii=False))
        return 0

    info(f"{len(presets_data)} presets, {sum(len(v) for v in presets_data.values())} base domains")
    info("Every entry also covers its subdomains; you do not need to list them.")
    info()
    for name in sorted(presets_data):
        domains = presets_data[name]
        info(f"{name} ({len(domains)})")
        if args.verbose:
            for domain in domains:
                info(f"  {domain}")
        else:
            info("  " + ", ".join(domains))
    info()
    info("Use `--all` to bypass every domain without maintaining a list.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    info("Zapret Turkey status")
    info("====================")
    info(f"service:   {run_capture(['systemctl', 'is-active', 'zapret'])}")
    info(f"resolved:  {run_capture(['systemctl', 'is-active', 'systemd-resolved'])}")
    info()

    if args.settings_file.exists():
        info(f"settings:  {args.settings_file}")
    else:
        info(f"settings:  {args.settings_file} (missing)")
    if args.module_file.exists():
        info(f"module:    {args.module_file}")
    else:
        info(f"module:    {args.module_file} (missing)")

    domains_file = Path("/etc/zapret-turkey/domains.txt")
    if domains_file.exists():
        count = len([line for line in domains_file.read_text().splitlines() if line.strip()])
        info(f"domains:   {count} entries in {domains_file}")
    info()

    exec_start = run_capture(["systemctl", "show", "zapret", "-p", "ExecStart", "--no-pager"])
    if exec_start:
        info(exec_start)
        info()

    rules = run_capture(["nft", "list", "table", "inet", "zapret"])
    if rules and "No such file" not in rules and "Operation not permitted" not in rules:
        info("nftables rules:")
        info(rules)
    else:
        iptables = run_capture(["iptables", "-t", "mangle", "-S"])
        matching = [line for line in iptables.splitlines() if "NFQUEUE" in line]
        if matching:
            info("iptables rules:")
            info("\n".join(matching))
    info()

    logs = run_capture(["journalctl", "-u", "zapret", "-n", "15", "--no-pager"])
    if logs:
        info("Recent logs:")
        info(logs)
    return 0


def check_url(url: str) -> str:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "zapret-turkey-doctor/2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            return f"HTTP {response.status}"
    except Exception as exc:  # noqa: BLE001 - report any network failure
        return f"failed ({exc})"


def cmd_doctor(args: argparse.Namespace) -> int:
    info("Zapret Turkey doctor")
    info("====================")

    os_release = Path("/etc/os-release")
    if os_release.exists():
        values = dict(
            line.split("=", 1)
            for line in os_release.read_text().splitlines()
            if "=" in line and not line.startswith("#")
        )
        info(f"NixOS:            {values.get('PRETTY_NAME', 'unknown').strip(chr(34))}")
    info(f"service:          {run_capture(['systemctl', 'is-active', 'zapret'])}")
    info(f"resolved:         {run_capture(['systemctl', 'is-active', 'systemd-resolved'])}")

    modules = ["nf_conntrack", "nfnetlink_queue", "xt_NFQUEUE", "xt_connbytes"]
    loaded = [name for name in modules if Path(f"/sys/module/{name}").exists()]
    info(f"kernel modules:   {', '.join(loaded) if loaded else 'none detected'}")

    nm = Path("/etc/nixos/configuration.nix")
    nft = run_capture(["nft", "list", "tables"])
    if "zapret" in nft:
        info("firewall:         nftables table inet zapret present")
    else:
        iptables = run_capture(["iptables", "-t", "mangle", "-S"])
        if "NFQUEUE" in iptables:
            info("firewall:         iptables NFQUEUE rules present")
        else:
            info("firewall:         no NFQUEUE rules found (need root? reboot?)")

    resolv = Path("/etc/resolv.conf")
    if resolv.exists():
        info("resolv.conf:")
        for line in resolv.read_text(errors="replace").splitlines()[:6]:
            info("  " + line)

    if not args.no_network:
        info()
        info("Connectivity checks:")
        for name, url in (
            ("discord", "https://discord.com"),
            ("roblox", "https://www.roblox.com"),
            ("youtube", "https://www.youtube.com"),
        ):
            info(f"  {name:<10} {check_url(url)}")

    info()
    info("If sites still fail:")
    info("  1. Tune services.zapret-turkey.ttl in /etc/nixos/zapret-turkey.nix.")
    info("  2. Run `zapret-turkey blockcheck` to find working nfqws parameters.")
    info("  3. Check `journalctl -u zapret -n 50 --no-pager` for errors.")
    if not nm.exists():
        info("  Note: /etc/nixos/configuration.nix was not found; pass --config to install.")
    return 0


def cmd_blockcheck(args: argparse.Namespace) -> int:
    require_root(args)
    firewall_package = "nixpkgs#iptables" if args.iptables else "nixpkgs#nftables"
    command = [
        "nix",
        "--extra-experimental-features",
        "nix-command flakes",
        "shell",
        "nixpkgs#zapret",
        firewall_package,
        "-c",
        "blockcheck",
    ]
    info("Running blockcheck. It may take a while and needs root for firewall changes.")
    try:
        os.execvp(command[0], command)
    except FileNotFoundError:
        die("nix was not found")
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def add_domain_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--presets",
        action="append",
        default=[],
        metavar="LIST",
        help="comma-separated preset names (default: discord,roblox)",
    )
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        metavar="NAME",
        help="add a preset by name (repeatable)",
    )
    parser.add_argument(
        "--domain",
        "--url",
        dest="domains",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="extra domain or URL (repeatable)",
    )
    parser.add_argument(
        "--domains",
        dest="domain_lists",
        action="append",
        default=[],
        metavar="LIST",
        help="comma-separated extra domains/URLs",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="domain to exclude, subdomains included (repeatable)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="bypass every domain instead of maintaining a whitelist",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zapret-turkey",
        description="Install and manage Zapret DPI bypass for Turkey on NixOS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  zapret-turkey install --presets discord,roblox,youtube\n"
            "  zapret-turkey install --all\n"
            "  zapret-turkey install --domain https://example.com/path\n"
            "  zapret-turkey domains --presets discord,roblox\n"
            "  zapret-turkey list-presets\n"
            "  zapret-turkey status\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    install = subparsers.add_parser("install", help="write the NixOS module and rebuild")
    add_domain_options(install)
    install.add_argument("--strategy", choices=["turkey", "custom"], default="turkey")
    install.add_argument("--ttl", type=int, default=3, help="fake-packet TTL (default: 3)")
    install.add_argument("--autottl", type=int, default=2, help="auto-TTL delta (default: 2)")
    install.add_argument("--fooling", default="md5sig", help="desync fooling method (default: md5sig)")
    install.add_argument("--repeats", type=int, default=6, help="fake repeats (default: 6)")
    install.add_argument(
        "--param",
        dest="params",
        action="append",
        default=[],
        metavar="FLAG",
        help="extra nfqws flag (repeatable; the full list with --strategy custom)",
    )
    install.add_argument("--no-dns", action="store_true", help="do not configure encrypted DNS")
    install.add_argument(
        "--dns",
        action="append",
        default=[],
        metavar="IP",
        help="DNS-over-TLS server (repeatable, default: Cloudflare + Google)",
    )
    install.add_argument("--dnssec", action="store_true", help="enable DNSSEC validation")
    install.add_argument("--no-http", action="store_true", help="do not route port 80")
    install.add_argument("--no-udp", action="store_true", help="do not route UDP")
    install.add_argument(
        "--no-firewall",
        action="store_true",
        help="do not install NFQUEUE rules (manage them yourself)",
    )
    install.add_argument("--qnum", type=int, default=200, help="NFQUEUE number (default: 200)")
    install.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="configuration.nix to edit (default: /etc/nixos/configuration.nix)",
    )
    install.add_argument("--settings-file", type=Path, default=DEFAULT_SETTINGS)
    install.add_argument("--module-file", type=Path, default=DEFAULT_MODULE)
    install.add_argument("--flake-attr", help="nixosConfigurations attribute for flake rebuilds")
    install.add_argument("--no-rebuild", action="store_true", help="write files but do not rebuild")
    install.add_argument("--dry-run", action="store_true", help="print what would happen")
    install.add_argument("--yes", "-y", action="store_true", help="overwrite without asking")
    install.set_defaults(func=cmd_install)

    uninstall = subparsers.add_parser("uninstall", help="remove the module and rebuild")
    uninstall.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="configuration.nix to edit (default: /etc/nixos/configuration.nix)",
    )
    uninstall.add_argument("--settings-file", type=Path, default=DEFAULT_SETTINGS)
    uninstall.add_argument("--module-file", type=Path, default=DEFAULT_MODULE)
    uninstall.add_argument("--flake-attr", help="nixosConfigurations attribute for flake rebuilds")
    uninstall.add_argument("--no-rebuild", action="store_true", help="remove files but do not rebuild")
    uninstall.add_argument("--dry-run", action="store_true", help="print what would happen")
    uninstall.add_argument("--yes", "-y", action="store_true", help=argparse.SUPPRESS)
    uninstall.set_defaults(func=cmd_uninstall)

    domains = subparsers.add_parser("domains", help="print the effective hostlist")
    add_domain_options(domains)
    domains.add_argument("--json", action="store_true", help="print JSON")
    domains.set_defaults(func=cmd_domains)

    presets = subparsers.add_parser("list-presets", help="show available domain presets")
    presets.add_argument("--verbose", "-v", action="store_true", help="print every domain")
    presets.add_argument("--json", action="store_true", help="print JSON")
    presets.set_defaults(func=cmd_list_presets)

    status = subparsers.add_parser("status", help="show service, DNS and firewall status")
    status.add_argument("--settings-file", type=Path, default=DEFAULT_SETTINGS, help=argparse.SUPPRESS)
    status.add_argument("--module-file", type=Path, default=DEFAULT_MODULE, help=argparse.SUPPRESS)
    status.set_defaults(func=cmd_status)

    doctor = subparsers.add_parser("doctor", help="run connectivity and configuration checks")
    doctor.add_argument("--no-network", action="store_true", help="skip HTTPS checks")
    doctor.set_defaults(func=cmd_doctor)

    blockcheck = subparsers.add_parser(
        "blockcheck",
        help="run zapret's blockcheck to find working nfqws parameters",
    )
    blockcheck.add_argument("--iptables", action="store_true", help="use iptables instead of nftables")
    blockcheck.set_defaults(func=cmd_blockcheck)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        arguments = ["install"]
    args = parser.parse_args(arguments)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
