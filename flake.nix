{
  description = "Zapret DPI bypass for Turkey on NixOS";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      mkPackage =
        pkgs:
        pkgs.runCommandLocal "zapret-turkey"
          {
            nativeBuildInputs = [ pkgs.makeWrapper ];
            meta = {
              description = "Install and manage Zapret DPI bypass for Turkey on NixOS";
              homepage = "https://github.com/noervthere/Zapret-Turkey-Configuration-For-Nixos";
              mainProgram = "zapret-turkey";
              platforms = systems;
            };
          }
          ''
            mkdir -p $out/bin $out/share/zapret-turkey
            cp ${./nix/zapret_turkey.py} $out/share/zapret-turkey/cli.py
            cp ${./nix/module.nix} $out/share/zapret-turkey/module.nix
            cp ${./nix/presets.json} $out/share/zapret-turkey/presets.json
            chmod +x $out/share/zapret-turkey/cli.py
            makeWrapper ${pkgs.python3}/bin/python3 $out/bin/zapret-turkey \
              --add-flags "$out/share/zapret-turkey/cli.py"
          '';
    in
    {
      packages = forAllSystems (system: {
        default = mkPackage nixpkgs.legacyPackages.${system};
        zapret-turkey = mkPackage nixpkgs.legacyPackages.${system};
      });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/zapret-turkey";
          meta.description = "Install and manage Zapret DPI bypass for Turkey on NixOS";
        };
      });

      nixosModules = {
        default = ./nix/module.nix;
        zapret-turkey = ./nix/module.nix;
      };

      overlays.default = final: _prev: {
        zapret-turkey = self.packages.${final.system}.default;
      };

      checks = forAllSystems (system: {
        package = self.packages.${system}.default;
      });
    };
}
