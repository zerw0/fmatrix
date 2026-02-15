{
  config,
  lib,
  fmatrix,
  ...
}:
let
  cfg = config.services.fmatrix;
in
{
  options.services.fmatrix = {
    enable = lib.mkEnableOption "A Matrix bot that shows your Last.fm stats directly in your rooms";
    matrix.homeserver = lib.mkOption {
      description = "Your Matrix homeserver URL";
      type = lib.types.str;
      example = "https://matrix.org";
    };
    matrix.user_id = lib.mkOption {
      description = "User ID of the fmatrix bot";
      type = lib.types.str;
      example = "@fmatrix:matrix.org";
    };
    matrix.device_id = lib.mkOption {
      description = "Device ID of the fmatrix bot (optional)";
      default = "FMBOT001";
      type = lib.types.str;
    };
    settings.command_prefix = lib.mkOption {
      description = "Command prefix";
      default = "!";
      type = lib.types.str;
    };
    settings.auto_join_rooms = lib.mkOption {
      description = "Optional: List of room IDs to auto-join on startup";
      default = [ ];
      example = [
        "!roomid1:server.com"
        "!roomid2:server.com"
      ];
      after = room: lib.strings.concatStringsSep ", " room;
      type = lib.types.list;
    };
    settings.log_level = lib.mkOption {
      default = "INFO";
      type = lib.types.enum [
        "INFO"
        "DEBUG"
        "WARNING"
        "ERROR"
      ];
    };
    stateDir = lib.mkOption {
      description = ''
        Directory below /var/lib to store fmarix data.
        This directory will be created automatically using systemd’s StateDirectory mechanism
      '';
      default = "fmatrix";
      type = lib.types.str;
    };
    secretsFile = lib.mkOption {
      description = "Path to a file with secrets";
      example = ''
        MATRIX_PASSWORD=your_secure_password_here
        LASTFM_API_KEY=your_lastfm_api_key_here
        LASTFM_API_SECRET=your_lastfm_api_secret_here
      '';
      type = lib.types.str;
    };

  };

  config = lib.mkIf cfg.enable {
    systemd.services.fmatrix = {
      description = "A Matrix bot that shows your Last.fm stats directly in your rooms";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = "${lib.getExe fmatrix} --config $\{CREDENTIALS_DIRECTORY\}/secrets.env";
        LoadCredential = "secrets.env:${cfg.secretsFile}";
        RestartSec = 1;
        Restart = "on-failure";
        RuntimeDirectory = "fmatrix";
        RuntimeDirectoryMode = "0700";
        DynamicUser = true;
        StateDirectory = cfg.stateDir;
        StateDirectoryMode = "0700";
        Environment = {
          MATRIX_HOMESERVER = cfg.matrix.homeserver;
          MATRIX_USER_ID = cfg.matrix.user_id;
          MATRIX_DEVICE_ID = cfg.matrix.device_id;
          COMMAND_PREFIX = cfg.settings.command_prefix;
          LOG_LEVEL = cfg.settings.log_level;
          DATA_DIR = config.systemd.services.fmatrix.stateDir;
          AUTO_JOIN_ROOMS = cfg.settings.auto_join_rooms;
        };
        EnvironmentFile = cfg.environmentFile;
        # Hardening
        DeviceAllow = [ "/dev/null rw" ];
        DevicePolicy = "strict";
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
        NoNewPrivileges = true;
        PrivateDevices = true;
        PrivateTmp = true;
        PrivateUsers = true;
        ProtectClock = true;
        ProtectControlGroups = true;
        ProtectHome = true;
        ProtectHostname = true;
        ProtectKernelLogs = true;
        ProtectKernelModules = true;
        ProtectKernelTunables = true;
        ProtectProc = "invisible";
        ProtectSystem = "full";
        RemoveIPC = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        SystemCallArchitectures = "native";
        SystemCallFilter = [
          "@system-service"
          "~@privileged"
        ];
      };
    };
  };
}
