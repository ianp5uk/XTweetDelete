// archive.js — optional local parser for X's official data export files
// (tweet.js and like.js from Settings > Your Account > Download an archive
// of your data). Used only as a fallback when the live API can't return a
// user's complete history for a category (see cap detection in app.js).
//
// Everything here runs entirely in the browser; files are never uploaded
// anywhere. The archive uses the legacy v1.1 tweet JSON shape wrapped in a
// JS assignment, e.g.:
//   window.YTD.tweet.part0 = [ { "tweet": { ... } }, ... ]
//   window.YTD.like.part0 = [ { "like": { "tweetId": "...", ... } }, ... ]

function extractJson(text) {
  const bracketIdx = text.indexOf("[");
  const braceIdx = text.indexOf("{");
  let start;
  if (bracketIdx === -1) start = braceIdx;
  else if (braceIdx === -1) start = bracketIdx;
  else start = Math.min(bracketIdx, braceIdx);

  if (start === -1) throw new Error("Doesn't look like an X archive data file.");

  // The archive wraps the array/object in a JS assignment ending with a
  // trailing semicolon (and sometimes trailing whitespace/newlines), which
  // is not valid JSON. Trim from the matching closing bracket instead of
  // just the opening one.
  const opener = text[start];
  const closer = opener === "[" ? "]" : "}";
  const end = text.lastIndexOf(closer);
  if (end === -1 || end < start) throw new Error("Doesn't look like an X archive data file.");

  return text.slice(start, end + 1);
}

// Archive dates look like "Wed Oct 10 20:19:24 +0000 2018" (legacy v1.1
// format) — Date() parses this correctly in all modern and most older
// browsers.
function toIso(archiveDate) {
  const d = new Date(archiveDate);
  if (isNaN(d.getTime())) return null;
  return d.toISOString();
}

// X (Twitter) Snowflake IDs encode a creation timestamp in their upper
// bits. like.js does not store a "date liked" at all (X has never exposed
// that via API or archive — only the liked post's own creation date is
// available, which is also all the live API gives you for likes). We
// decode it here so date-range filtering behaves identically whether a
// liked post came from the API or the archive.
function snowflakeToIso(id) {
  try {
    const idNum = BigInt(id);
    const twitterEpochMs = 1288834974657n; // 2010-11-04, X's Snowflake epoch
    const timestampMs = (idNum >> 22n) + twitterEpochMs;
    return new Date(Number(timestampMs)).toISOString();
  } catch {
    return null;
  }
}

async function readAndParse(file) {
  const text = await file.text();
  try {
    return JSON.parse(extractJson(text));
  } catch (e) {
    throw new Error(`Could not parse ${file.name}: ${e.message}`);
  }
}

// Reads File objects (from <input type="file" multiple>) and returns
// { posts: [], reposts: [], likes: [] } — each item shaped like the API's
// tweet objects: { id, created_at, referenced_tweets, source: "archive" }.
// posts/reposts come from tweet.js files; likes come from like.js files.
// Files are told apart by their contents, not filename, since export
// filenames vary slightly across archive versions.
export async function parseArchiveFiles(fileList) {
  const posts = new Map();
  const reposts = new Map();
  const likes = new Map();
  const warnings = [];

  for (const file of fileList) {
    const json = await readAndParse(file);
    if (!Array.isArray(json) || json.length === 0) continue;

    const first = json[0];
    const isLikeFile = !!(first && (first.like || first.tweetId));
    const isTweetFile = !!(first && (first.tweet || first.id_str || first.full_text));

    if (isLikeFile) {
      for (const entry of json) {
        const l = entry.like || entry;
        if (!l || !l.tweetId) continue;
        const createdIso = snowflakeToIso(l.tweetId);
        if (!createdIso) continue;
        likes.set(l.tweetId, {
          id: l.tweetId,
          created_at: createdIso,
          referenced_tweets: [],
          source: "archive",
        });
      }
    } else if (isTweetFile) {
      for (const entry of json) {
        const t = entry.tweet || entry;
        if (!t || !t.id_str) continue;
        const createdIso = toIso(t.created_at);
        if (!createdIso) continue;

        if (t.retweeted_status && t.retweeted_status.id_str) {
          // A retweet/repost. The API's unretweet endpoint needs the
          // ORIGINAL post's ID, which the legacy archive format nests here.
          reposts.set(t.id_str, {
            id: t.retweeted_status.id_str,
            created_at: createdIso, // when *you* reposted it
            referenced_tweets: [{ type: "retweeted", id: t.retweeted_status.id_str }],
            source: "archive",
          });
        } else if (/^RT @/.test(t.full_text || t.text || "") && !t.retweeted_status) {
          // Old-style manual "RT @user: ..." text repost with no structured
          // reference to the original post. Can't be undone via the
          // unretweet API (there's no source tweet ID to call it with), so
          // this is surfaced as a skipped item rather than silently
          // dropped or mis-deleted as a normal post.
          warnings.push(`Skipped an old-style "RT @" post (${t.id_str}) — no original post ID available to undo it.`);
        } else {
          posts.set(t.id_str, {
            id: t.id_str,
            created_at: createdIso,
            referenced_tweets: t.in_reply_to_status_id_str
              ? [{ type: "replied_to", id: t.in_reply_to_status_id_str }]
              : [],
            source: "archive",
          });
        }
      }
    } else {
      warnings.push(`${file.name} didn't look like a tweet.js or like.js archive file — skipped.`);
    }
  }

  return {
    posts: Array.from(posts.values()),
    reposts: Array.from(reposts.values()),
    likes: Array.from(likes.values()),
    warnings,
  };
}
