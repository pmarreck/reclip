{
  description = "ReClip — self-hosted video/audio downloader web UI (yt-dlp + Flask)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        python = pkgs.python312;

        # Python libraries — yt-dlp is used as a subprocess
        pythonEnv = python.withPackages (ps: with ps; [
          flask
          requests
          pytest
          responses
        ]);
      in
      {
        packages.default = pkgs.stdenv.mkDerivation {
          pname = "reclip";
          version = "0.1.0";
          src = ./.;

          nativeBuildInputs = [ pkgs.makeWrapper ];
          buildInputs = [ pythonEnv pkgs.ffmpeg ];

          dontBuild = true;

          installPhase = ''
            mkdir -p $out/share/reclip $out/bin
            cp -r app.py config.py cache.py llm_client.py service.py templates static assets $out/share/reclip/
            makeWrapper ${pythonEnv}/bin/python $out/bin/reclip \
              --add-flags "$out/share/reclip/app.py" \
              --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.ffmpeg pkgs.yt-dlp ]}
          '';
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [
            pythonEnv
            pkgs.ffmpeg
            pkgs.yt-dlp
          ];

          shellHook = ''
            echo "ReClip dev shell — python, flask, yt-dlp, ffmpeg all available"
            echo "Run: python app.py"
          '';
        };

        checks.test = pkgs.stdenv.mkDerivation {
          name = "reclip-test";
          src = ./.;

          nativeBuildInputs = [ pythonEnv ];

          dontBuild = true;

          checkPhase = ''
            export HOME=$TMPDIR
            export RECLIP_CONFIG_DIR=$TMPDIR/reclip-config
            python -m pytest tests/ -v
          '';

          doCheck = true;

          installPhase = ''
            mkdir -p $out
            echo "tests passed" > $out/result
          '';
        };

        # Idempotently patch a locally-installed oMLX.app so its bundled
        # transformers can construct WhisperProcessor (works around a
        # mistral_common <1.10 vs transformers 5.x version mismatch in the
        # oMLX 0.3.x bundle). Run with: `nix run .#fix-omlx`
        # See scripts/fix-omlx-stt.sh for the full explanation + revert/check
        # modes. Upstream tracking issue: jundot/omlx (TBD).
        apps.fix-omlx = {
          type = "app";
          meta = {
            description = "Patch a locally-installed oMLX.app so STT (Whisper) works";
          };
          program = toString (pkgs.writeShellScript "fix-omlx" ''
            export PATH="${pkgs.python312}/bin:$PATH"
            exec ${./scripts/fix-omlx-stt.sh} "$@"
          '');
        };
      }
    );
}
