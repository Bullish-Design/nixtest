# nixtest

A NixOS `nixos-container` (systemd-nspawn) test harness for `devenv.sh` environments.

## Overview

This repository provides a flake that includes:

- A NixOS module to declaratively define a pool of ephemeral containers.
- A runner CLI that snapshots a project into a slot and runs commands inside the container.
- Optional `nix run` support for the CLI.

## Flake outputs

- `nixosModules.devenvHarnessContainers`
- `packages.<system>.devenv-harness`
- `apps.<system>.devenv-harness`

## Host integration example

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    devenvHarness.url = "path:/path/to/your/nixtest";
  };

  outputs = { self, nixpkgs, devenvHarness, ... }:
    let
      system = "x86_64-linux";
    in {
      nixosConfigurations.myHost = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          devenvHarness.nixosModules.devenvHarnessContainers
          ({ pkgs, ... }: {
            services.devenvHarness.enable = true;
            services.devenvHarness.slots = 6;

            environment.systemPackages = [
              devenvHarness.packages.${system}.devenv-harness
            ];
          })
        ];
      };
    };
}
```

## Running tests

```bash
sudo devenv-harness --project . --cmd "devenv test"

sudo devenv-harness --project . \
  --cmd "nix flake check" \
  --cmd "devenv test" \
  --cmd "devenv shell -- bash -lc 'pytest -q'"
```
