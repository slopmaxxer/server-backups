{
  description = "Verified online backups for trough and slopd";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }: let
    systems = [ "x86_64-linux" "aarch64-linux" ];
    eachSystem = nixpkgs.lib.genAttrs systems;
  in {
    packages = eachSystem (system: let pkgs = nixpkgs.legacyPackages.${system}; in {
      default = pkgs.stdenvNoCC.mkDerivation {
        pname = "slop-services-backup";
        version = "0.1.0";
        src = self;
        nativeBuildInputs = [ pkgs.makeWrapper ];
        nativeCheckInputs = [ pkgs.python3 ];
        dontBuild = true;
        postPatch = ''
          substituteInPlace backup.py --replace-fail /usr/bin/sqlite3 ${pkgs.sqlite}/bin/sqlite3
        '';
        doCheck = true;
        checkPhase = "python3 -m unittest discover -s tests -v";
        installPhase = ''
          install -Dm644 backup.py $out/lib/slop-backups/backup.py
          makeWrapper ${pkgs.python3}/bin/python3 $out/bin/slop-services-backup \
            --add-flags "-I $out/lib/slop-backups/backup.py"
        '';
        meta = {
          description = "Verified online SQLite and media backups";
          license = pkgs.lib.licenses.mit;
          platforms = systems;
          mainProgram = "slop-services-backup";
        };
      };
    });
    checks = eachSystem (system: { package = self.packages.${system}.default; });
    devShells = eachSystem (system: {
      default = nixpkgs.legacyPackages.${system}.mkShell {
        packages = with nixpkgs.legacyPackages.${system}; [ python3 sqlite ];
      };
    });
    nixosModules.default = { config, lib, pkgs, ... }: let
      cfg = config.services.slop-services-backup;
    in {
      options.services.slop-services-backup = {
        enable = lib.mkEnableOption "verified trough/slopd backups";
        package = lib.mkOption {
          type = lib.types.package;
          default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
          description = "Backup executable package.";
        };
        output = lib.mkOption { type = lib.types.str; default = "/srv/backups/slop-services"; description = "Private archive directory."; };
        keep = lib.mkOption { type = lib.types.ints.positive; default = 14; description = "Successful archives retained."; };
        calendar = lib.mkOption { type = lib.types.str; default = "*-*-* 02:45:00 UTC"; description = "Backup schedule."; };
      };
      config = lib.mkIf cfg.enable {
        systemd.services.slop-services-backup = {
          description = "Verified local backups of trough and slopd";
          serviceConfig = {
            Type = "oneshot";
            ExecStart = "${lib.getExe cfg.package} --output ${lib.escapeShellArg cfg.output} --keep ${toString cfg.keep}";
            UMask = "0077";
            Nice = 10;
            IOSchedulingClass = "best-effort";
            IOSchedulingPriority = 7;
            TimeoutStartSec = "10min";
            PrivateTmp = true;
            ProtectHome = true;
            PrivateNetwork = true;
          };
        };
        systemd.timers.slop-services-backup = {
          wantedBy = [ "timers.target" ];
          timerConfig = { OnCalendar = cfg.calendar; RandomizedDelaySec = "15min"; Persistent = true; };
        };
      };
    };
  };
}
