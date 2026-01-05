{ lib, config, pkgs, ... }:

let
  cfg = config.services.devenvHarness;

  mkName = i: "${cfg.containerPrefix}${toString i}";
  slotDir = i: "${cfg.stateDir}/slots/${toString i}";
  slotWork = i: "${slotDir i}/work";

  slotIds = lib.range 1 cfg.slots;

  mkContainer = i: {
    name = mkName i;
    value = {
      autoStart = false;
      ephemeral = true;

      privateNetwork = cfg.privateNetwork;
      hostAddress = lib.mkIf cfg.privateNetwork "192.168.100.${toString (10 + i)}";
      localAddress = lib.mkIf cfg.privateNetwork "192.168.100.${toString (100 + i)}";

      bindMounts."/work" = {
        hostPath = slotWork i;
        isReadOnly = false;
      };

      tmpfs = cfg.tmpfs;
      extraFlags = cfg.extraFlags;

      config = cfg.containerConfig;
    };
  };

in {
  options.services.devenvHarness = {
    enable = lib.mkEnableOption "devenv.sh container test harness";

    slots = lib.mkOption {
      type = lib.types.int;
      default = 4;
      description = "Number of parallel container slots.";
    };

    containerPrefix = lib.mkOption {
      type = lib.types.str;
      default = "devenv-harness-";
      description = "Container name prefix; final name is prefix + slot number.";
    };

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/devenv-harness";
      description = "Host state directory for slot workspaces and locks.";
    };

    artifactsDirDefault = lib.mkOption {
      type = lib.types.str;
      default = "./artifacts";
      description = "Default artifact output root (runner can override).";
    };

    privateNetwork = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Whether harness containers use a private network namespace.";
    };

    natExternalInterface = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "If privateNetwork=true, set this to the host's external NIC for NAT.";
    };

    tmpfs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "List of tmpfs mounts in the container (systemd-nspawn --tmpfs args).";
    };

    extraFlags = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Extra systemd-nspawn flags to pass to the container.";
    };

    containerConfig = lib.mkOption {
      type = lib.types.deferredModule;
      default = { pkgs, ... }: {
        boot.isContainer = true;

        environment.systemPackages = with pkgs; [
          bash
          coreutils
          git
        ];

        nix.settings.experimental-features = [ "nix-command" "flakes" ];

        users.users.runner = {
          isNormalUser = true;
          home = "/home/runner";
          createHome = true;
          extraGroups = [ "wheel" ];
        };

        system.stateVersion = config.system.stateVersion;
      };
      description = "NixOS module used as the container's configuration.";
    };
  };

  config = lib.mkIf cfg.enable {
    boot.enableContainers = true;

    networking.nat = lib.mkIf cfg.privateNetwork {
      enable = true;
      internalInterfaces = [ "ve-+" ];
      externalInterface = lib.mkIf (cfg.natExternalInterface != null) cfg.natExternalInterface;
    };

    systemd.tmpfiles.rules =
      [
        "d ${cfg.stateDir} 0750 root root -"
        "d ${cfg.stateDir}/slots 0750 root root -"
      ]
      ++ (lib.concatMap (i: [
        "d ${slotDir i} 0750 root root -"
        "d ${slotWork i} 0750 root root -"
        "f ${slotDir i}/slot.lock 0640 root root -"
      ]) slotIds);

    environment.etc."devenv-harness/config.json".text =
      builtins.toJSON {
        slots = cfg.slots;
        containerPrefix = cfg.containerPrefix;
        stateDir = cfg.stateDir;
        artifactsDirDefault = cfg.artifactsDirDefault;
      };

    containers = lib.listToAttrs (map mkContainer slotIds);
  };
}
