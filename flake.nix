{
  description = "devenv.sh test harness using native NixOS containers";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.devenv.url = "github:cachix/devenv";

  outputs = { self, nixpkgs, devenv, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
    in
    {
      nixosModules.devenvHarnessContainers = import ./modules/devenv-harness-containers.nix;

      packages = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          devenv-harness = pkgs.python3Packages.buildPythonApplication {
            pname = "devenv-harness";
            version = "0.1.0";
            format = "other";
            src = ./pkgs/devenv-harness-runner;
            dontBuild = true;
            installPhase = ''
              mkdir -p $out/bin
              install -m755 runner.py $out/bin/devenv-harness
            '';
            propagatedBuildInputs = [ ];
          };
        });

      apps = forAllSystems (system: {
        devenv-harness = {
          type = "app";
          program = "${self.packages.${system}.devenv-harness}/bin/devenv-harness";
        };
      });
    };
}
