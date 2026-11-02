# Zapret DPI bypass for Turkey — NixOS module.

{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.zapret-turkey;

  presets = builtins.fromJSON (builtins.readFile ./presets.json);
  presetNames = builtins.attrNames presets;

  normalizeDomain =
    raw:
    let
      s = lib.toLower (lib.strings.trim raw);
      noScheme = lib.removePrefix "https://" (lib.removePrefix "http://" s);
      noAuth = if lib.hasInfix "@" noScheme then lib.last (lib.splitString "@" noScheme) else noScheme;
      noFragment = lib.head (lib.splitString "#" noAuth);
      noQuery = lib.head (lib.splitString "?" noFragment);
      noPath = lib.head (lib.splitString "/" noQuery);
      noPort = if lib.hasInfix ":" noPath then lib.head (lib.splitString ":" noPath) else noPath;
      noWildcard = lib.removePrefix "*." (lib.removePrefix "." noPort);
      noWww = lib.removePrefix "www." noWildcard;
    in
    lib.removeSuffix "." noWww;

  presetDomains = lib.concatMap (name: presets.${name}) cfg.presets;
  normalizedDomains = lib.unique (map normalizeDomain (presetDomains ++ cfg.extraDomains));
  excludedDomains = lib.unique (map normalizeDomain cfg.excludeDomains);
  isExcluded =
    domain:
    lib.any (excluded: domain == excluded || lib.hasSuffix ".${excluded}" domain) excludedDomains;
  effectiveDomains = lib.filter (domain: !(isExcluded domain)) normalizedDomains;

  hasNftables = config.networking.nftables.enable;
  nftPort = port: lib.replaceStrings [ ":" ] [ "-" ] port;
  nftUdpPorts = lib.concatStringsSep ", " (map nftPort cfg.udpPorts);

  generatedParams = [
    "--dpi-desync=${if cfg.strategy == "turkey" then "fake" else "fake,disorder2"}"
  ]
  ++ lib.optional (cfg.ttl != null) "--dpi-desync-ttl=${toString cfg.ttl}"
  ++ lib.optional (cfg.autottl != null) "--dpi-desync-autottl=${toString cfg.autottl}"
  ++ lib.optional (cfg.fooling != "") "--dpi-desync-fooling=${cfg.fooling}"
  ++ [ "--dpi-desync-any-protocol" ]
  ++ lib.optional (cfg.repeats != null) "--dpi-desync-repeats=${toString cfg.repeats}"
  ++ cfg.extraParams;

  zapretHasUdpOptions = lib.versionAtLeast lib.version "25.05pre";
  resolvedUsesSettings = lib.versionAtLeast lib.version "26.05pre";
in
{
  options.services.zapret-turkey = {
    enable = lib.mkEnableOption "the Zapret DPI bypass service tuned for Turkey";

    presets = lib.mkOption {
      type = lib.types.listOf (lib.types.enum presetNames);
      default = [
        "discord"
        "roblox"
      ];
      example = [
        "discord"
        "roblox"
        "youtube"
        "twitter"
        "instagram"
        "telegram"
      ];
      description = ''
        Curated domain presets to bypass. Subdomains are covered automatically.
        Run `zapret-turkey list-presets` to see the available names.
      '';
    };

    extraDomains = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [
        "example.com"
        "https://www.example.net/some/path"
      ];
      description = ''
        Extra domains to bypass. Bare domains, `*.domain` and full URLs are
        accepted; the host is extracted and normalized automatically.
      '';
    };

    excludeDomains = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = ''
        Domains to remove from the whitelist. Subdomains are excluded too.
        With `bypassAll`, these become the blacklist instead.
      '';
    };

    bypassAll = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Bypass every domain instead of using a whitelist. This is the
        zero-maintenance mode, at the cost of processing more traffic.
      '';
    };

    strategy = lib.mkOption {
      type = lib.types.enum [
        "turkey"
        "custom"
      ];
      default = "turkey";
      description = ''
        Packet strategy. `turkey` uses the known-good fake/TTL parameters.
        Use `custom` together with `params` for full control.
      '';
    };

    ttl = lib.mkOption {
      type = lib.types.nullOr lib.types.int;
      default = 3;
      description = "TTL for generated fake packets (`--dpi-desync-ttl`). Set to null to omit.";
    };

    autottl = lib.mkOption {
      type = lib.types.nullOr lib.types.int;
      default = 2;
      description = "Auto-TTL delta (`--dpi-desync-autottl`). Set to null to omit.";
    };

    fooling = lib.mkOption {
      type = lib.types.str;
      default = "md5sig";
      description = "Fooling method(s) for fake packets (`--dpi-desync-fooling`). Empty string omits it.";
    };

    repeats = lib.mkOption {
      type = lib.types.nullOr lib.types.int;
      default = 6;
      description = "How many times fake packets are sent (`--dpi-desync-repeats`). Set to null to omit.";
    };

    params = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = if cfg.strategy == "custom" then cfg.extraParams else generatedParams;
      example = [
        "--dpi-desync=fake,disorder2"
        "--dpi-desync-ttl=1"
        "--dpi-desync-autottl=2"
      ];
      description = ''
        Full nfqws parameter list. When set explicitly, it replaces the
        generated strategy parameters.
      '';
    };

    extraParams = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = ''
        Extra nfqws flags appended to the generated parameters. With
        `strategy = "custom"`, these are the only parameters used.
      '';
    };

    qnum = lib.mkOption {
      type = lib.types.int;
      default = 200;
      description = "NFQUEUE number. Change only if it conflicts with another service.";
    };

    httpSupport = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Route HTTP (port 80) as well as HTTPS.";
    };

    httpMode = lib.mkOption {
      type = lib.types.enum [
        "first"
        "full"
      ];
      default = "first";
      description = "HTTP processing mode. Use `full` if the DPI inspects whole sessions.";
    };

    udpSupport = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Route UDP traffic (QUIC, Discord voice, Roblox, STUN/TURN).";
    };

    udpPorts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "443"
        "3478"
        "3479"
        "3480"
        "3481"
        "50000:65535"
      ];
      description = "UDP ports/ranges to route. Ranges use `:` (converted to nftables `-` automatically).";
    };

    manageFirewall = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Let this module install the NFQUEUE rules. Handles both iptables and
        nftables. Disable if you manage the firewall yourself.
      '';
    };

    secureDns = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Enable systemd-resolved with DNS-over-TLS and route all queries to the
        configured DNS servers. This fixes Turkish ISP DNS poisoning.
      '';
    };

    dnsServers = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "1.1.1.1"
        "1.0.0.1"
        "8.8.8.8"
        "8.8.4.4"
      ];
      description = "DNS servers used when `secureDns` is enabled. They must support DNS-over-TLS.";
    };

    dnssec = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable DNSSEC validation. Disabled by default because it can break some networks.";
    };

    effectiveDomains = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      readOnly = true;
      default = effectiveDomains;
      description = "The computed whitelist, useful for debugging (`nix eval`).";
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = cfg.strategy != "custom" || cfg.params != [ ];
            message = "services.zapret-turkey.strategy = \"custom\" requires a non-empty services.zapret-turkey.params.";
          }
          {
            assertion = !cfg.udpSupport || cfg.udpPorts != [ ];
            message = "services.zapret-turkey.udpSupport requires at least one udpPorts entry.";
          }
        ];

        warnings =
          lib.optional (!cfg.bypassAll && effectiveDomains == [ ])
            "services.zapret-turkey has no domains configured; zapret will bypass all domains."
          ++ lib.optional (
            cfg.manageFirewall && !hasNftables && !config.networking.firewall.enable
          ) "services.zapret-turkey.manageFirewall = true, but networking.firewall is disabled and nftables is not enabled. Enable the firewall or manage NFQUEUE rules yourself."
          ++ lib.optional (!zapretHasUdpOptions && cfg.udpSupport && !hasNftables)
            "services.zapret-turkey.udpSupport needs NixOS 25.05 or newer with the iptables backend. Use nftables or upgrade NixOS.";

        services.zapret = {
          enable = true;
          configureFirewall = cfg.manageFirewall && !hasNftables;
          params = cfg.params;
          whitelist =
            if zapretHasUdpOptions then
              (if cfg.bypassAll then [ ] else effectiveDomains)
            else
              (if cfg.bypassAll || effectiveDomains == [ ] then null else effectiveDomains);
          blacklist =
            if zapretHasUdpOptions then
              (if cfg.bypassAll then excludedDomains else [ ])
            else
              (if cfg.bypassAll && excludedDomains != [ ] then excludedDomains else null);
          qnum = cfg.qnum;
          httpSupport = cfg.httpSupport;
        }
        // lib.optionalAttrs zapretHasUdpOptions {
          httpMode = cfg.httpMode;
          udpSupport = cfg.udpSupport;
          udpPorts = cfg.udpPorts;
        };

        boot.kernelModules = [
          "nf_conntrack"
          "nfnetlink_queue"
        ]
        ++ lib.optionals (!hasNftables) [
          "xt_NFQUEUE"
          "xt_connbytes"
          "xt_multiport"
        ];
      }

      (lib.mkIf (!cfg.bypassAll) {
        environment.etc."zapret-turkey/domains.txt".text =
          lib.concatStringsSep "\n" effectiveDomains + "\n";
      })

      (lib.mkIf cfg.secureDns {
        networking.nameservers = lib.mkDefault cfg.dnsServers;
        networking.dhcpcd.extraConfig = lib.mkDefault "nohook resolv.conf";
        services.resolved =
          {
            enable = true;
          }
          // (
            if resolvedUsesSettings then
              {
                settings.Resolve = {
                  DNS = cfg.dnsServers;
                  FallbackDNS = cfg.dnsServers;
                  Domains = [ "~." ];
                  DNSSEC = if cfg.dnssec then "yes" else "no";
                  DNSOverTLS = "yes";
                };
              }
            else
              {
                dnssec = if cfg.dnssec then "true" else "false";
                dnsovertls = "true";
                fallbackDns = cfg.dnsServers;
                extraConfig = "Domains=~.";
              }
          );
      })

      (lib.mkIf (cfg.manageFirewall && hasNftables) {
        networking.nftables.ruleset = lib.mkAfter (
          "table inet zapret {\n"
          + "  chain postrouting {\n"
          + "    type filter hook postrouting priority mangle; policy accept;\n"
          + "\n"
          + "    # HTTPS\n"
          + "    tcp dport 443 ct original packets 1-6 meta mark & 0x40000000 == 0 queue num ${toString cfg.qnum} bypass\n"
          + lib.optionalString cfg.httpSupport (
            "    # HTTP\n"
            + "    tcp dport 80 ${
                  lib.optionalString (cfg.httpMode == "first") "ct original packets 1-6 "
                }meta mark & 0x40000000 == 0 queue num ${toString cfg.qnum} bypass\n"
          )
          + lib.optionalString cfg.udpSupport (
            "    # UDP (QUIC, Discord voice/RTC, Roblox, STUN/TURN)\n"
            + "    udp dport { ${nftUdpPorts} } meta mark & 0x40000000 == 0 queue num ${toString cfg.qnum} bypass\n"
          )
          + "  }\n"
          + "}\n"
        );
      })
    ]
  );
}
