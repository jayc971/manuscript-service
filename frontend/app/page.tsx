"use client";
import { useState, useEffect, useCallback, useRef } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const STAGE_NAMES = ["validated", "text_extracted", "similarity_checked", "preview_built", "author_notified"];

type Stage = { name: string; status: string; attempts: number; last_error: string | null };
type Manuscript = {
  id: string; title: string; author_email: string; status: string;
  editor_status: string; priority: string; needs_attention: boolean;
  word_count: number | null; similarity_score: number | null;
  created_at: string; stages: Stage[];
};

const STATUS_TIPS: Record<string, string> = {
  received:   "Queued - waiting for a worker to pick it up.",
  processing: "Pipeline is actively running through the stages.",
  done:       "All 5 stages completed successfully.",
  error:      "One or more stages failed after the maximum retries.",
};

function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  return (
    <span style={{ display: "inline-flex", alignItems: "center" }}
      onMouseMove={e => setPos({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setPos(null)}>
      {children}
      {pos && (
        <span style={{
          position: "fixed", left: pos.x + 12, top: pos.y - 36,
          whiteSpace: "nowrap", background: "#1e293b", color: "#f1f5f9",
          fontSize: 11, padding: "4px 8px", borderRadius: 4,
          pointerEvents: "none", boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
          zIndex: 9999, border: "1px solid #334155",
        }}>{text}</span>
      )}
    </span>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
      <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
    </svg>
  );
}
function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  );
}
function EyeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
    </svg>
  );
}
function EyeOffIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
      <line x1="1" y1="1" x2="23" y2="23"/>
    </svg>
  );
}

function parseJwt(token: string): { sub: string; role: string } | null {
  try {
    const b64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(b64));
  } catch { return null; }
}

const statusDot: Record<string, string> = {
  received: "#6b7280", processing: "#3b82f6", done: "#16a34a", error: "#dc2626",
};
const stageDot: Record<string, string> = {
  pending: "#9ca3af", running: "#60a5fa", done: "#4ade80", error: "#f87171", retrying: "#fbbf24",
};

type Theme = "light" | "dark";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    const saved = localStorage.getItem("theme") as Theme | null;
    const resolved = saved ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    setTheme(resolved);
    document.documentElement.dataset.theme = resolved;
  }, []);
  const toggle = () => setTheme(t => {
    const next = t === "light" ? "dark" : "light";
    localStorage.setItem("theme", next);
    document.documentElement.dataset.theme = next;
    return next;
  });
  return [theme, toggle];
}

export default function Home() {
  const [theme, toggleTheme] = useTheme();
  const [token, setToken] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [manuscripts, setManuscripts] = useState<Manuscript[]>([]);
  const [loginError, setLoginError] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitMsg, setSubmitMsg] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [droppedFile, setDroppedFile] = useState<File | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 10;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const dark = theme === "dark";
  const c = {
    bg:       dark ? "#0f1117" : "#f5f7fa",
    surface:  dark ? "#1a1d27" : "#f9fafb",
    border:   dark ? "#2e3242" : "#e5e7eb",
    fg:       dark ? "#f1f5f9" : "#111827",
    muted:    dark ? "#94a3b8" : "#6b7280",
    input:    dark ? "#1e2130" : "#f0f4f8",
    inputFg:  dark ? "#f1f5f9" : "#111827",
    inputBdr: dark ? "#3e4457" : "#d1d5db",
    btnBg:    dark ? "#334155" : "#1e293b",
    btnFg:    dark ? "#e2e8f0" : "#f0f4f8",
  };

  useEffect(() => {
    const saved = localStorage.getItem("token");
    if (saved) {
      const payload = parseJwt(saved);
      if (payload) { setToken(saved); setRole(payload.role); }
    }
  }, []);

  const fetchManuscripts = useCallback(async (tok: string, userRole: string, initial = false) => {
    if (initial) setListLoading(true);
    try {
      const url = userRole === "editor" ? `${API}/manuscripts/all` : `${API}/manuscripts`;
      const res = await fetch(url, { headers: { Authorization: `Bearer ${tok}` } });
      if (!res.ok) throw new Error(`Server error: ${res.status}`);
      setManuscripts(await res.json());
      setListError("");
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Failed to load manuscripts.");
    } finally {
      if (initial) setListLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!token || !role) return;
    fetchManuscripts(token, role, true);
    pollRef.current = setInterval(() => fetchManuscripts(token, role), 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [token, role, fetchManuscripts]);

  async function login(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setLoginError("");
    const fd = new FormData(e.currentTarget);
    const res = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: fd.get("email"), password: fd.get("password") }),
    });
    if (!res.ok) { setLoginError("Wrong email or password."); return; }
    const { access_token } = await res.json();
    const payload = parseJwt(access_token);
    if (!payload) { setLoginError("Invalid token received."); return; }
    localStorage.setItem("token", access_token);
    setToken(access_token);
    setRole(payload.role);
  }

  function logout() {
    localStorage.removeItem("token");
    setToken(null); setRole(null); setManuscripts([]);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (!file) return;
    if (!file.name.endsWith(".txt")) { setSubmitMsg("Error: Only .txt files are accepted."); return; }
    setDroppedFile(file);
    setSubmitMsg("");
    if (fileInputRef.current) {
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInputRef.current.files = dt.files;
    }
  }

  async function submitManuscript(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!droppedFile && !fileInputRef.current?.files?.length) {
      alert("Please select or drop a .txt file before submitting.");
      return;
    }
    setSubmitMsg(""); setSubmitting(true);
    const fd = new FormData(e.currentTarget);
    if (droppedFile && (!fd.get("file") || (fd.get("file") as File).size === 0)) {
      fd.set("file", droppedFile);
    }
    const res = await fetch(`${API}/manuscripts`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: fd,
    });
    setSubmitting(false);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      setSubmitMsg(`Error: ${err.detail ?? res.statusText}`);
      return;
    }
    const { id } = await res.json();
    setSubmitMsg(`Submitted - ID: ${id}`);
    setDroppedFile(null);
    (e.target as HTMLFormElement).reset();
    fetchManuscripts(token!, role!);
  }

  async function updateEditorStatus(manuscriptId: string, status: string) {
    await fetch(`${API}/manuscripts/${manuscriptId}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ status }),
    });
    fetchManuscripts(token!, role!);
  }

  const inp: React.CSSProperties = {
    padding: "8px 12px",
    border: `1px solid ${c.inputBdr}`,
    borderRadius: 6,
    fontSize: 14,
    outline: "none",
    width: "100%",
    boxSizing: "border-box",
    background: c.input,
    color: c.inputFg,
  };

  if (!token) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: c.bg, fontFamily: "var(--font-geist-sans)" }}>
        <div style={{ width: 340, padding: 32, border: `1px solid ${c.border}`, borderRadius: 8, background: c.surface }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
            <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0, color: c.fg }}>Manuscript Service</h1>
            <button onClick={toggleTheme} title="Toggle theme" style={{ background: "none", border: "none", cursor: "pointer", color: c.muted, display: "flex", alignItems: "center" }}>{dark ? <SunIcon /> : <MoonIcon />}</button>
          </div>
          <form onSubmit={login} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <input name="email" type="email" required placeholder="Email" defaultValue="author@example.com" style={inp} />
            <div style={{ position: "relative" }}>
              <input name="password" type={showPassword ? "text" : "password"} required placeholder="Password" defaultValue="password123" style={{ ...inp, paddingRight: 40 }} />
              <button type="button" onClick={() => setShowPassword(v => !v)} tabIndex={-1}
                style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: c.muted, fontSize: 16, lineHeight: 1, padding: 0 }}
                title={showPassword ? "Hide password" : "Show password"}>
                {showPassword ? <EyeOffIcon /> : <EyeIcon />}
              </button>
            </div>
            {loginError && <p style={{ color: "#dc2626", fontSize: 13, margin: 0 }}>{loginError}</p>}
            <button type="submit" style={{ background: c.btnBg, color: c.btnFg, border: "none", borderRadius: 6, padding: "8px 16px", fontSize: 14, cursor: "pointer", fontWeight: 600, marginTop: 8 }}>
              Log in
            </button>
          </form>
          <p style={{ marginTop: 16, fontSize: 12, color: c.muted, lineHeight: 1.6, textAlign: "center" }}>
            author@example.com / password123<br />
            editor@example.com / password123
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: c.bg, color: c.fg, fontFamily: "var(--font-geist-sans)" }}>
      <div style={{ maxWidth: 960, margin: "0 auto", padding: "32px 20px" }}>

        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 32, borderBottom: `1px solid ${c.border}`, paddingBottom: 16 }}>
          <div>
            <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0, color: c.fg }}>Manuscript Service</h1>
            <span style={{ fontSize: 13, color: c.muted }}>Logged in as <strong>{role}</strong></span>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <button onClick={toggleTheme} title="Toggle theme" style={{ background: "none", border: "none", cursor: "pointer", color: c.muted, display: "flex", alignItems: "center" }}>{dark ? <SunIcon /> : <MoonIcon />}</button>
            <button onClick={logout} style={{ background: c.surface, color: c.fg, border: `1px solid ${c.border}`, borderRadius: 6, padding: "6px 14px", fontSize: 13, cursor: "pointer" }}>Log out</button>
          </div>
        </header>

        {role === "author" && (
          <section style={{ marginBottom: 40 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0, color: c.fg }}>Submit a Manuscript</h2>
              {submitMsg && <span style={{ fontSize: 13, color: submitMsg.startsWith("Error") ? "#dc2626" : "#16a34a" }}>{submitMsg}</span>}
            </div>
            <form onSubmit={submitManuscript}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "stretch" }}>
                {/* Left: fields + submit */}
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <input name="title" required placeholder="Title" style={inp} />
                  <input name="author_email" type="email" required placeholder="Author email" style={inp} />
                  <select name="priority" style={{ ...inp, appearance: "none", backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2394a3b8' d='M6 8L1 3h10z'/%3E%3C/svg%3E")`, backgroundRepeat: "no-repeat", backgroundPosition: "right 12px center", paddingRight: 32 }}>
                    <option value="normal">Normal priority</option>
                    <option value="high">High priority</option>
                  </select>
                  <button type="submit" disabled={submitting} style={{ background: c.btnBg, color: c.btnFg, border: "none", borderRadius: 6, padding: "8px 16px", fontSize: 14, cursor: "pointer", fontWeight: 600, marginTop: "auto" }}>
                    {submitting ? "Submitting..." : "Submit"}
                  </button>
                </div>

                {/* Right: drop zone */}
                <div style={{ display: "flex", flexDirection: "column" }}>
                <div
                  onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                  style={{
                    border: `2px dashed ${dragOver ? "#60a5fa" : c.inputBdr}`,
                    borderRadius: 8, cursor: "pointer",
                    background: dragOver ? (dark ? "#1e2d40" : "#eff6ff") : c.input,
                    transition: "all 0.15s",
                    display: "flex", flexDirection: "column",
                    alignItems: "center", justifyContent: "center", gap: 8,
                    flex: 1, minHeight: 160, padding: 24,
                  }}
                >
                  <span style={{ fontSize: 32 }}>{droppedFile ? "📄" : "📂"}</span>
                  <span style={{ fontSize: 13, color: droppedFile ? c.fg : c.muted, textAlign: "center", lineHeight: 1.5 }}>
                    {droppedFile ? droppedFile.name : <>Drop a <strong>.txt</strong> file here<br />or click to browse</>}
                  </span>
                  {droppedFile && (
                    <button type="button" onClick={e => { e.stopPropagation(); setDroppedFile(null); if (fileInputRef.current) fileInputRef.current.value = ""; }}
                      style={{ fontSize: 11, color: c.muted, background: "none", border: "none", cursor: "pointer", textDecoration: "underline" }}>
                      Remove
                    </button>
                  )}
                  <input ref={fileInputRef} name="file" type="file" accept=".txt"
                    onChange={e => { const f = e.target.files?.[0] ?? null; setDroppedFile(f); setSubmitMsg(""); }}
                    style={{ display: "none" }} />
                </div>
                </div>
              </div>
            </form>
          </section>
        )}

        <section>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0, color: c.fg }}>{role === "editor" ? "All Manuscripts" : "My Manuscripts"}</h2>
            <span style={{ fontSize: 12, color: c.muted }}>Auto-refreshes every 5 s</span>
          </div>

          {listLoading ? (
            <div style={{ display: "flex", alignItems: "center", gap: 10, color: c.muted, fontSize: 14, padding: "24px 0" }}>
              <span style={{ width: 16, height: 16, border: `2px solid ${c.border}`, borderTopColor: "#60a5fa", borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
              Loading manuscripts...
              <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            </div>
          ) : listError ? (
            <div style={{ padding: "16px", borderRadius: 6, background: "#fee2e2", color: "#b91c1c", fontSize: 13, border: "1px solid #fca5a5" }}>
              {listError} — <button onClick={() => fetchManuscripts(token!, role!, true)} style={{ background: "none", border: "none", color: "#b91c1c", cursor: "pointer", textDecoration: "underline", fontSize: 13 }}>Retry</button>
            </div>
          ) : manuscripts.length === 0 ? (
            <p style={{ color: c.muted, fontSize: 14 }}>No manuscripts yet.</p>
          ) : (() => {
            const totalPages = Math.ceil(manuscripts.length / PAGE_SIZE);
            const pageRows = manuscripts.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
            return (
              <>
                <div style={{ overflowX: "auto", border: `1px solid ${c.border}`, borderRadius: 8 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, background: c.surface }}>
                    <thead>
                      <tr style={{ borderBottom: `2px solid ${c.border}` }}>
                        {["ID", "Title", ...(role === "editor" ? ["Author"] : []), "Status", "Decision", "Stages", "Words", "Score", ...(role === "editor" ? ["Action"] : [])].map(h => (
                          <th key={h} style={{ padding: "10px 12px", fontWeight: 600, fontSize: 11, letterSpacing: "0.06em", textTransform: "uppercase", color: c.muted, textAlign: "left" }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {pageRows.map((m, i) => (
                        <tr key={m.id} style={{ borderBottom: i < pageRows.length - 1 ? `1px solid ${c.border}` : "none" }}>
                          <td style={{ padding: "10px 12px", color: c.fg }}><code style={{ fontSize: 11, color: c.muted }}>{m.id}</code></td>
                          <td style={{ padding: "10px 12px", color: c.fg }}>
                            {m.needs_attention && (
                              <Tooltip text="Similarity score >= 70% - may contain content similar to existing works. Editor review recommended.">
                                <span style={{ marginRight: 5, cursor: "help" }}>⚠️</span>
                              </Tooltip>
                            )}
                            {m.title}
                          </td>
                          {role === "editor" && <td style={{ padding: "10px 12px", color: c.muted, fontSize: 12 }}>{m.author_email}</td>}
                          <td style={{ padding: "10px 12px" }}>
                            <Tooltip text={STATUS_TIPS[m.status] ?? m.status}>
                              <span style={{ padding: "2px 8px", borderRadius: 4, background: statusDot[m.status] + "30", color: statusDot[m.status], fontWeight: 700, fontSize: 11, cursor: "help" }}>
                                {m.status}
                              </span>
                            </Tooltip>
                          </td>
                          <td style={{ padding: "10px 12px" }}>
                            {(() => {
                              const decisionColor: Record<string, string> = { pending: "#6b7280", accepted: "#16a34a", rejected: "#dc2626", revision_requested: "#d97706" };
                              const col = decisionColor[m.editor_status] ?? "#6b7280";
                              return (
                                <span style={{ padding: "2px 8px", borderRadius: 4, background: col + "25", color: col, fontWeight: 700, fontSize: 11 }}>
                                  {m.editor_status.replace("_", " ")}
                                </span>
                              );
                            })()}
                          </td>
                          <td style={{ padding: "10px 12px" }}>
                            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                              {STAGE_NAMES.map(name => {
                                const s = m.stages.find(x => x.name === name);
                                const tip = `${name}: ${s?.status ?? "pending"}${s?.last_error ? " - " + s.last_error : ""}`;
                                return (
                                  <Tooltip key={name} text={tip}>
                                    <span style={{ width: 10, height: 10, borderRadius: "50%", background: stageDot[s?.status ?? "pending"], flexShrink: 0, cursor: "help", display: "inline-block" }} />
                                  </Tooltip>
                                );
                              })}
                            </div>
                          </td>
                          <td style={{ padding: "10px 12px", color: m.word_count != null ? c.fg : c.muted, fontVariantNumeric: "tabular-nums" }}>{m.word_count ?? "-"}</td>
                          <td style={{ padding: "10px 12px", color: m.similarity_score != null ? c.fg : c.muted, fontVariantNumeric: "tabular-nums" }}>{m.similarity_score != null ? `${m.similarity_score}` : "-"}</td>
                          {role === "editor" && (
                            <td style={{ padding: "10px 12px" }}>
                              <select value={m.editor_status} onChange={e => updateEditorStatus(m.id, e.target.value)}
                                style={{ fontSize: 12, padding: "4px 8px", border: `1px solid ${c.inputBdr}`, borderRadius: 4, background: c.input, color: c.inputFg, cursor: "pointer" }}>
                                <option value="pending">Pending</option>
                                <option value="accepted">Accepted</option>
                                <option value="rejected">Rejected</option>
                                <option value="revision_requested">Needs revision</option>
                              </select>
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {totalPages > 1 && (
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 4, marginTop: 10 }}>
                    <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: c.muted }}>
                      {Object.entries(stageDot).map(([s, col]) => (
                        <span key={s} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                          <span style={{ width: 9, height: 9, borderRadius: "50%", background: col, flexShrink: 0 }} />{s}
                        </span>
                      ))}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    {[
                      { label: "«", action: () => setPage(0), disabled: page === 0, title: "First" },
                      { label: "‹", action: () => setPage(p => Math.max(0, p - 1)), disabled: page === 0, title: "Previous" },
                    ].map(btn => (
                      <button key={btn.label} onClick={btn.action} disabled={btn.disabled} title={btn.title}
                        style={{ fontSize: 14, width: 28, height: 28, border: `1px solid ${c.border}`, borderRadius: 4, background: c.surface, color: btn.disabled ? c.muted : c.fg, cursor: btn.disabled ? "default" : "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}>
                        {btn.label}
                      </button>
                    ))}
                    <span style={{ fontSize: 12, color: c.fg, padding: "0 6px", minWidth: 24, textAlign: "center" }}>{page + 1}</span>
                    {[
                      { label: "›", action: () => setPage(p => Math.min(totalPages - 1, p + 1)), disabled: page === totalPages - 1, title: "Next" },
                      { label: "»", action: () => setPage(totalPages - 1), disabled: page === totalPages - 1, title: "Last" },
                    ].map(btn => (
                      <button key={btn.label} onClick={btn.action} disabled={btn.disabled} title={btn.title}
                        style={{ fontSize: 14, width: 28, height: 28, border: `1px solid ${c.border}`, borderRadius: 4, background: c.surface, color: btn.disabled ? c.muted : c.fg, cursor: btn.disabled ? "default" : "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}>
                        {btn.label}
                      </button>
                    ))}
                    </div>
                  </div>
                )}
                {totalPages <= 1 && (
                  <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: c.muted, marginTop: 10 }}>
                    {Object.entries(stageDot).map(([s, col]) => (
                      <span key={s} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <span style={{ width: 9, height: 9, borderRadius: "50%", background: col, flexShrink: 0 }} />{s}
                      </span>
                    ))}
                  </div>
                )}
              </>
            );
          })()}
        </section>

      </div>
    </div>
  );
}
