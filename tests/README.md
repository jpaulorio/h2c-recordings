# Verification

Run `bun test`, `bun run check`, and `bun run test:python` after installing dependencies. The automated suites use mocks or synthetic videos and do not access a real printer.

The optional fixture serves synthetic MP4 chunks over real implicit TLS FTP. It generates a temporary self-signed certificate using OpenSSL and creates two sessions with two chunks each, plus non-video files to verify filtering.

From the repository, run these in separate terminals (choose an absolute scratch folder):

```bash
.venv/bin/python tests/ftps_fixture.py /tmp/h2c-test-fixture
H2C_CONFIG_FILE=/tmp/h2c-test-fixture/settings.json \
H2C_DATA_DIR=/tmp/h2c-test-fixture/app-state \
PORT=3211 bun run start
```

For an automated API check, run `.venv/bin/python tests/api_smoke.py` against the running test app. It verifies multi-job downloading, cancellation, merging, library access, byte ranges, and API access checks.

Open http://127.0.0.1:3211. Select both sessions, enqueue them, and verify they download sequentially, merge, and appear in Videos. Play a video and seek, then check cancellation and retry. The fixture credential is `fixture-only` and is not a real printer credential. All fixture output belongs in scratch storage outside the repository.

To slow transfers for progress/cancellation checks, set `H2C_FIXTURE_DELAY=0.5` on the fixture command. Stop both processes when finished. Do not point this fixture at your actual printer or real settings file.
