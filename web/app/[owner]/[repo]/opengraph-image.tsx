import { ImageResponse } from "next/og";
import { getRepo } from "@/lib/api";
import { languageNames } from "@/lib/format";
import { isValidRepoPath } from "@/lib/github";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "RepoLens: an indexed GitHub repository";

const INK = "#1c1a17";
const MUTED = "#66615a";
const BRAND = "#0e7c6b";

export default async function Image({ params }: { params: Promise<{ owner: string; repo: string }> }) {
  const { owner, repo: name } = await params;
  const repo = isValidRepoPath(owner, name) ? await getRepo(`${owner}/${name}`).catch(() => null) : null;
  const s = repo?.stats;
  const language = s ? languageNames(s.chunks_by_language) : "";

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 64,
          backgroundColor: "#fbfaf7",
          borderTop: `12px solid ${BRAND}`,
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 30, fontWeight: 700, color: INK }}>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
            <circle cx="10" cy="10" r="6.5" stroke={BRAND} strokeWidth="2.2" />
            <path d="M15 15l5.5 5.5" stroke={BRAND} strokeWidth="2.4" strokeLinecap="round" />
            <path d="M7.4 9.2l1.6 1.6 3-3.2" stroke={BRAND} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          RepoLens
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {language && <div style={{ display: "flex", fontSize: 28, color: MUTED }}>{language} repository</div>}
          <div style={{ display: "flex", fontSize: 76, fontWeight: 700, color: INK, letterSpacing: -2, lineHeight: 1.05 }}>
            {repo ? repo.repo_id : `${owner}/${name}`}
          </div>
          <div style={{ display: "flex", fontSize: 32, color: INK }}>
            {repo ? "Ask it anything. Answers cite the exact lines." : "Not indexed yet"}
          </div>
        </div>

        {s ? (
          <div style={{ display: "flex", gap: 48, fontSize: 26, color: MUTED }}>
            <Fact n={s.files_indexed} label="files" />
            <Fact n={s.chunks} label="functions & classes" />
            <Fact n={s.graph.edges ?? 0} label="imports mapped" />
          </div>
        ) : (
          <div style={{ display: "flex" }} />
        )}
      </div>
    ),
    { ...size }
  );
}

function Fact({ n, label }: { n: number; label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
      <span style={{ fontSize: 40, fontWeight: 700, color: BRAND }}>{n.toLocaleString("en-US")}</span>
      <span>{label}</span>
    </div>
  );
}
