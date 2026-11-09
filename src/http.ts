export function fileResponse(
  file: ReturnType<typeof Bun.file>,
  request: Request,
  name: string,
) {
  const headers = new Headers({
    "Content-Type": "video/mp4",
    "Accept-Ranges": "bytes",
    "Cache-Control": "no-store",
    "Content-Disposition": `${new URL(request.url).searchParams.has("download") ? "attachment" : "inline"}; filename*=UTF-8''${encodeURIComponent(name)}`,
  });
  const size = file.size;
  const range = request.headers.get("range");
  if (!range) {
    headers.set("Content-Length", String(size));
    return new Response(request.method === "HEAD" ? null : file, { headers });
  }
  const match = /^bytes=(\d*)-(\d*)$/.exec(range);
  if (!match || (!match[1] && !match[2]))
    return new Response(null, {
      status: 416,
      headers: { "Content-Range": `bytes */${size}` },
    });
  const start = match[1]
    ? Number(match[1])
    : Math.max(0, size - Number(match[2]));
  const end = match[1]
    ? Math.min(size - 1, match[2] ? Number(match[2]) : size - 1)
    : size - 1;
  if (
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(end) ||
    start < 0 ||
    start >= size ||
    end < start
  )
    return new Response(null, {
      status: 416,
      headers: { "Content-Range": `bytes */${size}` },
    });
  headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
  headers.set("Content-Length", String(end - start + 1));
  return new Response(
    request.method === "HEAD" ? null : file.slice(start, end + 1),
    { status: 206, headers },
  );
}
