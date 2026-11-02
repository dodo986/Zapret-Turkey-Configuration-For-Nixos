# Zapret Turkey Configuration for NixOS

> **Bypass Deep Packet Inspection (DPI) and regional blocks in Turkey on NixOS.**
> Made for personal use — you may still need to tune it for your ISP.

[![NixOS](https://img.shields.io/badge/NixOS-24.11%2B-blue?logo=nixos&logoColor=white)](https://nixos.org)
[![flake](https://img.shields.io/badge/install-nix%20run-informational)](https://nixos.org)

---

## What this does

- Runs [zapret](https://github.com/bol-van/zapret) through the native NixOS
  `services.zapret` module.
- Ships **domain presets** for the services people usually need in Turkey
  (Discord, Roblox, YouTube, Instagram, X/Twitter, Telegram, TikTok, …).
- Expands **one base domain into all of its subdomains** automatically, so you
  do not have to copy hundreds of CDN hostnames into `zapret.nix`.
- Accepts full URLs, wildcards and bare domains:
  `https://www.example.com/some/path` becomes `example.com`.
- Configures encrypted DNS via systemd-resolved to avoid Turkish ISP DNS
  poisoning.
- Supports **both iptables and nftables** firewall backends, including the newer
  NixOS generations where `networking.nftables.enable = true` is common.
- Can be installed with `nix run` or used as a normal flake input.

## Requirements

- NixOS **24.11 or newer** (25.05+, 26.05 and unstable are all handled).
  UDP support on the iptables backend requires 25.05+; use nftables on 24.11.
- Flakes enabled (`experimental-features = nix-command flakes`).
- Root access for the install/rebuild step.

## Quick start (nix run)

```sh
nix run github:noervthere/Zapret-Turkey-Configuration-For-Nixos -- install
```

The installer will:

1. Ask for `sudo` if needed.
2. Write `/etc/nixos/zapret-turkey.nix` (your editable settings).
3. Write `/etc/nixos/zapret-turkey-module.nix` (the module with presets inlined).
4. Add `./zapret-turkey.nix` to the `imports` list in
   `/etc/nixos/configuration.nix`.
5. Run `nixos-rebuild switch` (or a flake rebuild if `/etc/nixos/flake.nix`
   exists). For a Git-backed `/etc/nixos` flake, it stages the generated files
   so Nix can see them; you can commit them afterwards.

The default preset set is `discord` + `roblox`. Add more in one command:

```sh
nix run github:noervthere/Zapret-Turkey-Configuration-For-Nixos -- \
  install --presets discord,roblox,youtube,twitter,instagram,telegram,tiktok
```

Or bypass every domain and never maintain a list:

```sh
nix run github:noervthere/Zapret-Turkey-Configuration-For-Nixos -- install --all
```

Useful installer options:

| Option | Meaning |
| --- | --- |
| `--presets discord,roblox,...` | Choose maintained domain presets |
| `--presets all` | Select every maintained preset |
| `--all` | Bypass every domain (no whitelist) |
| `--domain URL` | Add your own site; URLs and wildcards are accepted |
| `--exclude DOMAIN` | Remove a domain (and its subdomains) |
| `--ttl N` | Tune the fake-packet TTL (default `3`) |
| `--no-dns` | Leave system DNS untouched |
| `--no-rebuild` | Write files but do not rebuild |
| `--dry-run` | Show what would be written/changed |
| `--yes` | Overwrite existing settings without asking |

## Flake usage

If you manage your system with a flake, import the module and set options
declaratively:

```nix
{
  inputs.zapret-turkey.url = "github:noervthere/Zapret-Turkey-Configuration-For-Nixos";
  # Optional: reuse your system nixpkgs for the CLI package.
  inputs.zapret-turkey.inputs.nixpkgs.follows = "nixpkgs";

  outputs = { self, nixpkgs, zapret-turkey, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        zapret-turkey.nixosModules.default
        {
          services.zapret-turkey = {
            enable = true;
            presets = [ "discord" "roblox" "youtube" ];
            extraDomains = [
              "example.com"
              "https://www.example.net/some/path" # normalized to example.net
            ];
          };
        }
      ];
    };
  };
}
```

Then rebuild as usual:

```sh
sudo nixos-rebuild switch --flake /etc/nixos#myhost
```

### Module options

| Option | Default | Description |
| --- | --- | --- |
| `enable` | `false` | Turn the module on |
| `presets` | `[ "discord" "roblox" ]` | Domain presets to bypass |
| `extraDomains` | `[ ]` | Extra domains/URLs |
| `excludeDomains` | `[ ]` | Domains to remove from the list |
| `bypassAll` | `false` | Ignore the whitelist and bypass everything |
| `strategy` | `"turkey"` | `turkey` or `custom` |
| `ttl` | `3` | `--dpi-desync-ttl` value |
| `autottl` | `2` | `--dpi-desync-autottl` value |
| `fooling` | `"md5sig"` | `--dpi-desync-fooling` value |
| `repeats` | `6` | `--dpi-desync-repeats` value |
| `params` | generated | Full nfqws parameter list (overrides the above) |
| `extraParams` | `[ ]` | Extra nfqws flags appended to the generated list |
| `qnum` | `200` | NFQUEUE number |
| `httpSupport` | `true` | Route HTTP port 80 too |
| `httpMode` | `"first"` | `first` or `full` |
| `udpSupport` | `true` | Route UDP (QUIC, Discord voice, Roblox) |
| `udpPorts` | `[ "443" "3478" … "50000:65535" ]` | UDP ports/ranges |
| `manageFirewall` | `true` | Install NFQUEUE rules for iptables/nftables |
| `secureDns` | `true` | Enable systemd-resolved with DNS-over-TLS |
| `dnsServers` | Cloudflare + Google | DNS-over-TLS servers |
| `dnssec` | `false` | Enable DNSSEC validation |

`presets` names are validated. See the full list with:

```sh
nix run .# -- list-presets
```

## Available presets

`discord`, `roblox`, `youtube`, `twitter`, `instagram`, `facebook`,
`whatsapp`, `telegram`, `tiktok`, `netflix`, `spotify`, `steam`, `reddit`,
`twitch`, `wikipedia`, `proton`, `signal`, `pinterest`, `soundcloud`,
`dailymotion`, `vimeo`.

Each preset contains only base domains. Zapret's hostlist automatically covers
subdomains, which is why `roblox.com` is enough for `auth.roblox.com`,
`games.roblox.com`, `t3.rbxcdn.com`, and so on.

## Managing an installation

```sh
# See the effective hostlist without installing anything
nix run .# -- domains --presets discord,roblox,youtube

# Check the service, DNS and firewall rules
nix run .# -- status

# Run connectivity and configuration checks
nix run .# -- doctor

# Find working nfqws parameters for your ISP
nix run .# -- blockcheck

# Remove the module and rebuild
nix run .# -- uninstall
```

When the repository is cloned locally, the old command still works:

```sh
git clone https://github.com/noervthere/Zapret-Turkey-Configuration-For-Nixos
cd Zapret-Turkey-Configuration-For-Nixos
sudo ./install-zapret.sh
```

## Tuning

Network conditions vary between Turk Telekom, Turkcell Superonline, Kablonet and
smaller ISPs. If sites still fail after installation:

1. Open `/etc/nixos/zapret-turkey.nix`.
2. Tune `ttl` first (try `1`, `2`, `5`, `8`).
3. If that does not help, run `zapret-turkey blockcheck` and copy the working
   parameters into `params`.
4. Rebuild with `sudo nixos-rebuild switch`.
5. Check the logs with `journalctl -u zapret -n 50 --no-pager`.

If DNS itself stops working, your ISP may block DNS-over-TLS on port 853. Set
`services.zapret-turkey.secureDns = false;` (or run the installer with
`--no-dns`) and rebuild.

You can also try a completely different desync mode without editing the module:

```nix
services.zapret-turkey.params = [
  "--dpi-desync=fake,disorder2"
  "--dpi-desync-ttl=1"
  "--dpi-desync-autottl=2"
  "--dpi-desync-any-protocol"
];
```

## Uninstall

```sh
nix run github:noervthere/Zapret-Turkey-Configuration-For-Nixos -- uninstall
```

This removes the import, backs up the two generated files and rebuilds. To
uninstall manually, remove `./zapret-turkey.nix` from `imports` in
`/etc/nixos/configuration.nix`, delete the two `zapret-turkey*.nix` files and
rebuild.

## How it works

Zapret sends manipulated packets through `nfqws` so the DPI sees a harmless
request while the real server receives the original one. The module:

- Writes a `--hostlist` containing the selected presets and your extra domains.
- Routes the relevant ports through NFQUEUE and lets `nfqws` decide which
  hostnames to process.
- Uses `--dpi-desync=fake` with a low TTL by default.
- Optionally rotates DNS through systemd-resolved with DNS-over-TLS.

## Disclaimer

This repository is provided as-is for educational purposes and connectivity
restoration. The maintainer is not responsible for misuse or local policy
violations.
