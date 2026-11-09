import { expect, test } from "bun:test";
import { fileResponse } from "../src/http";
import { writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
test("video streaming supports full, ranged, suffix and invalid requests", async () => {
  const path = join(tmpdir(), randomUUID() + ".mp4");
  writeFileSync(path, "0123456789");
  try {
    const file = Bun.file(path);
    const req = (range?: string) =>
      new Request("http://localhost/api/videos/x", {
        headers: range ? { range } : {},
      });
    let response = fileResponse(file, req(), "video.mp4");
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("0123456789");
    response = fileResponse(file, req("bytes=2-5"), "video.mp4");
    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 2-5/10");
    expect(await response.text()).toBe("2345");
    expect(await fileResponse(file, req("bytes=-3"), "v.mp4").text()).toBe(
      "789",
    );
    expect(await fileResponse(file, req("bytes=7-"), "v.mp4").text()).toBe(
      "789",
    );
    for (const range of [
      "bytes=10-20",
      "bytes=5-2",
      "bytes=-0",
      "bytes=",
      "bytes=0-1,4-5",
    ])
      expect(fileResponse(file, req(range), "v.mp4").status).toBe(416);
    const head = fileResponse(
      file,
      new Request("http://localhost", { method: "HEAD" }),
      "v.mp4",
    );
    expect(await head.text()).toBe("");
    expect(head.headers.get("Content-Length")).toBe("10");
  } finally {
    unlinkSync(path);
  }
});
