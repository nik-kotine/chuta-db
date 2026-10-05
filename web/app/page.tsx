"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, CircleStop, Copy, Database, FileBox, History, LockKeyhole, Play, RefreshCw, RotateCcw, Search, Table2, Terminal, X, Zap } from "lucide-react";
import styles from "./page.module.css";
import type { SpatialPoint } from "./MapPanel";

const MapPanel = dynamic(() => import("./MapPanel"), { ssr: false });

type Column = { name: string; type: string; position: number; primary_key: boolean };
type TableInfo = { name: string; file_type: string; key_index: number; columns: Column[]; indexes: { index_name: string; index_type: string; column_name: string }[] };
type PlanNode = { node: string; table?: string; index?: string; operation?: string; access?: string; column?: string; rows?: number };
type QueryResult = { message: string; columns: string[]; rows: unknown[][]; plan: PlanNode[]; row_count: number; affected_rows?: number; duration_ms?: number };
type HistoryItem = { sql: string; status: "ok" | "error"; duration: number; rows: number; at: string };
type DemoEvent = { type: string; label: string; detail: string; tone: "neutral" | "active" | "success" | "warning" };
type LiveTransactionStatus = { transactions: { transaction_id: number; status: string; last_lsn: number }[]; locks: { resource: string; transaction_id: number | null; mode: string; count: number }[]; wal: { lsn: number; prev_lsn: number; transaction_id: number; type: string; operation: string | null; resource?: string }[]; recovery: { recovered_transactions: number[]; last_checkpoint: number | null; redo_records: number; clr_records: number } };
type TransactionDetail = { transaction_id: number; status: string; last_lsn: number; locks: { resource: string; mode: string; count: number }[]; events: { type: string; lsn: number; operation: string | null; resource: string }[] };

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const starterSql = "SELECT *\nFROM ventas\nWHERE id BETWEEN 1 AND 10\nORDER BY id ASC\nLIMIT 25;";

export default function Home() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [sql, setSql] = useState(starterSql);
  const [result, setResult] = useState<QueryResult>({ message: "Ejecuta una consulta para ver sus resultados.", columns: [], rows: [], plan: [], row_count: 0 });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<"queries" | "transactions">("queries");
  const [toast, setToast] = useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [demoScenario, setDemoScenario] = useState("commit");
  const [demoStep, setDemoStep] = useState(0);
  const [demoPlaying, setDemoPlaying] = useState(false);
  const [liveStatus, setLiveStatus] = useState<LiveTransactionStatus | null>(null);
  const [sessionId, setSessionId] = useState("browser-demo");
  const [selectedTransactionId, setSelectedTransactionId] = useState<number | null>(null);
  const [transactionDetail, setTransactionDetail] = useState<TransactionDetail | null>(null);
  const [spatialPoints, setSpatialPoints] = useState<SpatialPoint[]>([]);
  const [demoRunning, setDemoRunning] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const [page, setPage] = useState(0);
  const pageSize = 12;

  async function loadTables() {
    try {
      const response = await fetch(`${API}/api/tables`);
      if (!response.ok) throw new Error("No se pudo cargar el catálogo");
      const data = await response.json();
      setTables(data.tables);
      setSelected((current) => current ?? data.tables[0]?.name ?? null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Error de conexión");
    }
  }

  async function runQuery(query = sql) {
    if (!query.trim()) {
      setError("Escribe una consulta antes de ejecutar.");
      setToast({ tone: "error", text: "No hay una consulta para ejecutar" });
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const started = performance.now();
    setLoading(true);
    setError("");
    setPage(0);
    try {
      const response = await fetch(`${API}/api/query`, { method: "POST", headers: { "Content-Type": "application/json", "X-Session-ID": sessionId }, body: JSON.stringify({ sql: query }), signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "La consulta no pudo ejecutarse");
      const elapsed = data.duration_ms ?? performance.now() - started;
      const affected = data.affected_rows ?? data.row_count ?? 0;
      setResult(data);
      setDuration(elapsed);
      setHistory((items) => [{ sql: query, status: "ok" as const, duration: elapsed, rows: affected, at: new Date().toLocaleTimeString() }, ...items].slice(0, 8));
      setToast({ tone: "success", text: `${data.message ?? "Consulta ejecutada"} · ${elapsed.toFixed(0)} ms` });
      loadTables();
    } catch (queryError) {
      if (queryError instanceof DOMException && queryError.name === "AbortError") {
        setToast({ tone: "info", text: "Consulta cancelada" });
        return;
      }
      const elapsed = performance.now() - started;
      setError(queryError instanceof Error ? queryError.message : "Error de consulta");
      setDuration(elapsed);
      setHistory((items) => [{ sql: query, status: "error" as const, duration: elapsed, rows: 0, at: new Date().toLocaleTimeString() }, ...items].slice(0, 8));
      setToast({ tone: "error", text: queryError instanceof Error ? queryError.message : "Error de consulta" });
    } finally {
      setLoading(false);
      controllerRef.current = null;
    }
  }

  function cancelQuery() { controllerRef.current?.abort(); }
  function syncEditorScroll() {
    if (gutterRef.current && editorRef.current) gutterRef.current.scrollTop = editorRef.current.scrollTop;
  }
  function copySql() { void navigator.clipboard?.writeText(sql); setToast({ tone: "info", text: "SQL copiado al portapapeles" }); }

  useEffect(() => {
    loadTables();
    const saved = window.localStorage.getItem("chuta-query-history");
    if (saved) setHistory(JSON.parse(saved));
    const savedSession = window.sessionStorage.getItem("chuta-session-id");
    const nextSession = savedSession ?? `tab-${crypto.randomUUID()}`;
    setSessionId(nextSession);
    window.sessionStorage.setItem("chuta-session-id", nextSession);
  }, []);
  useEffect(() => { window.localStorage.setItem("chuta-query-history", JSON.stringify(history)); }, [history]);
  
  useEffect(() => {
    // Si la consulta actual tiene resultados, intentamos extraer los puntos directamente del resultado
    if (result.columns.length > 0 && result.rows.length > 0) {
      const lonIdx = result.columns.findIndex(c => ["longitude", "lon", "lng", "x"].includes(c.toLowerCase()));
      const latIdx = result.columns.findIndex(c => ["latitude", "lat", "y"].includes(c.toLowerCase()));
      
      let points: SpatialPoint[] = [];
      // Usar la tabla seleccionada para que MapPanel no oculte los puntos
      const currentTable = selected ?? "Resultado de consulta";

      if (lonIdx !== -1 && latIdx !== -1) {
        points = result.rows.map((row, i) => ({
          table: currentTable,
          rid: [0, i] as [number, number],
          longitude: Number(row[lonIdx]),
          latitude: Number(row[latIdx]),
          label: String(row[1] ?? row[0] ?? `Punto ${i + 1}`), // Intenta usar la columna de nombre
          values: row
        })).filter(p => !isNaN(p.longitude) && !isNaN(p.latitude));
      } else {
        // Buscar la columna nativa POINT (un arreglo [lon, lat] en JSON)
        const pointIdx = result.rows[0].findIndex(val => Array.isArray(val) && val.length === 2 && typeof val[0] === "number");
        if (pointIdx !== -1) {
          points = result.rows.map((row, i) => {
            const pt = row[pointIdx] as [number, number];
            return {
              table: currentTable,
              rid: [0, i] as [number, number],
              longitude: pt[0], // Longitud
              latitude: pt[1],  // Latitud
              label: String(row[1] ?? row[0] ?? `Punto ${i + 1}`), // Intenta usar la columna de nombre
              values: row
            };
          });
        }
      }

      // Si encontramos puntos en la query actual, actualizar mapa e ignorar el endpoint global
      if (points.length > 0) {
        setSpatialPoints(points);
        return; 
      }
    }

    fetch(`${API}/api/spatial/points${selected ? `?table=${encodeURIComponent(selected)}` : ""}`)
      .then((response) => response.ok ? response.json() : { points: [] })
      .then((data) => setSpatialPoints(data.points ?? []))
      .catch(() => setSpatialPoints([]));
  }, [selected, result]);
  

  useEffect(() => { if (!demoPlaying) return; const timer = window.setInterval(() => setDemoStep((step) => step + 1), 1100); return () => window.clearInterval(timer); }, [demoPlaying]);
  useEffect(() => {
    if (view !== "transactions") return;
    let cancelled = false;
    async function loadLiveStatus() {
      try {
        const responses = await Promise.all([fetch(`${API}/api/transactions`), fetch(`${API}/api/locks`), fetch(`${API}/api/wal`), fetch(`${API}/api/recovery/status`)]);
        if (responses.some((response) => !response.ok)) return;
        const [transactions, locks, wal, recovery] = await Promise.all(responses.map((response) => response.json()));
        if (!cancelled) setLiveStatus({ transactions: transactions.transactions, locks: locks.locks, wal: wal.records, recovery });
      } catch { if (!cancelled) setLiveStatus(null); }
    }
    loadLiveStatus();
    const timer = window.setInterval(loadLiveStatus, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [view]);
  useEffect(() => {
    if (selectedTransactionId === null) { setTransactionDetail(null); return; }
    let cancelled = false;
    fetch(`${API}/api/transactions/${selectedTransactionId}`).then((response) => response.ok ? response.json() : null).then((data) => { if (!cancelled) setTransactionDetail(data); }).catch(() => { if (!cancelled) setTransactionDetail(null); });
    return () => { cancelled = true; };
  }, [selectedTransactionId]);
  const activeTable = tables.find((table) => table.name === selected);
  const visibleRows = result.rows.slice(page * pageSize, (page + 1) * pageSize);
  const pageCount = Math.max(1, Math.ceil(result.rows.length / pageSize));
  const demoScenarios: Record<string, { title: string; sql: string; events: DemoEvent[]; runnable: boolean; requirement?: string }> = {
    commit: { title: "Commit normal", sql: "BEGIN TRANSACTION;\nINSERT INTO demo_ventas VALUES (7, 'Demo', 450.0);\nEND TRANSACTION;", runnable: true, events: [{ type: "BEGIN", label: "Transacción iniciada", detail: "TX-07 · ACTIVE", tone: "active" }, { type: "EXCLUSIVE", label: "Lock adquirido", detail: "demo_ventas · escritura", tone: "warning" }, { type: "UPDATE", label: "Mutación registrada", detail: "WAL · before/after", tone: "neutral" }, { type: "COMMIT", label: "Commit durable", detail: "force() · locks liberados", tone: "success" }] },
    rollback: { title: "Rollback", sql: "BEGIN TRANSACTION;\nUPDATE demo_ventas SET monto = 999.0 WHERE id = 1;\nROLLBACK;", runnable: true, events: [{ type: "BEGIN", label: "Transacción iniciada", detail: "TX-08 · ACTIVE", tone: "active" }, { type: "UREAD", label: "Intención de actualización", detail: "demo_ventas · lector compatible", tone: "warning" }, { type: "EXCLUSIVE", label: "Promoción a escritura", detail: "before/after registrados", tone: "warning" }, { type: "CLR", label: "Undo aplicado", detail: "fila restaurada · ABORTED", tone: "success" }] },
    timeout: { title: "Timeout de lock", sql: "TX A: SELECT * FROM demo_ventas;\nTX B: UPDATE demo_ventas SET monto = 110.0 WHERE id = 1;", runnable: false, requirement: "requiere dos sesiones del navegador", events: [{ type: "SHARED", label: "TX-A lee demo_ventas", detail: "lectura compartida", tone: "active" }, { type: "UREAD", label: "TX-B espera", detail: "intención de actualización", tone: "warning" }, { type: "EXCLUSIVE", label: "Escritura bloqueada", detail: "TX-A conserva SHARED", tone: "warning" }, { type: "TIMEOUT", label: "LockTimeoutError", detail: "TX-B abortada y limpiada", tone: "success" }] },
    recovery: { title: "Crash y recovery", sql: "BEGIN TRANSACTION;\nINSERT INTO demo_ventas VALUES (8, 'Uncommitted', 700.0);", runnable: false, requirement: "requiere detener y reiniciar el backend", events: [{ type: "WAL", label: "WAL durable", detail: "UPDATE · before/after", tone: "neutral" }, { type: "CRASH", label: "Cierre inesperado", detail: "transacción sin COMMIT", tone: "warning" }, { type: "REDO", label: "Redo de confirmadas", detail: "páginas e índices", tone: "active" }, { type: "UNDO", label: "Undo de incompletas", detail: "CLR · estado consistente", tone: "success" }] },
  };
  const demo = demoScenarios[demoScenario];
  const activeDemoStep = Math.min(demoStep, demo.events.length - 1);
  function chooseDemo(value: string) { setDemoScenario(value); setDemoStep(0); setDemoPlaying(false); }
  async function runDemo() {
    if (!demo.runnable) {
      setToast({ tone: "info", text: `${demo.title}: ${demo.requirement}` });
      return;
    }
    setDemoRunning(true);
    setDemoStep(0);
    setToast({ tone: "info", text: `Ejecutando demo real: ${demo.title}` });
    try {
      for (const statement of demo.sql.split("\n").filter(Boolean)) {
        await runQuery(statement);
      }
      const transactionResponse = await fetch(`${API}/api/transactions`);
      if (transactionResponse.ok) {
        const transactionData = await transactionResponse.json();
        const latest = [...transactionData.transactions].sort((a, b) => b.transaction_id - a.transaction_id)[0];
        if (latest) setSelectedTransactionId(latest.transaction_id);
      }
      setDemoStep(demo.events.length - 1);
      setToast({ tone: "success", text: `Demo real completada: ${demo.title}` });
    } finally {
      setDemoRunning(false);
    }
  }
  const liveTimeline = liveStatus?.wal.slice(-12).reverse() ?? [];
  const selectedTimeline = transactionDetail?.events.map((event) => ({
    lsn: event.lsn,
    prev_lsn: 0,
    transaction_id: transactionDetail.transaction_id,
    type: event.type,
    operation: event.operation,
  })).reverse() ?? [];
  const eventExplanation: Record<string, string> = { BEGIN: "La transacción comienza", UPDATE: "Se registra una modificación en el WAL", COMMIT: "Los cambios quedan confirmados y durables", ABORT: "La transacción se cancela", CLR: "Se compensa una operación durante undo", CHECKPOINT: "Se guarda un punto de recuperación" };

  return (
    <main className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.brand}><div className={styles.brandMark}><Database size={19} /></div><div><strong>CHUTA DB</strong><span>QUERY TERMINAL / 01</span></div></div>
        <nav className={styles.viewTabs} aria-label="Vistas de la consola">
          <button className={view === "queries" ? styles.viewTabActive : styles.viewTab} onClick={() => setView("queries")}><Terminal size={14} /> Consultas</button>
          <button className={view === "transactions" ? styles.viewTabActive : styles.viewTab} onClick={() => setView("transactions")}><LockKeyhole size={14} /> Transacciones</button>
        </nav>
        <div className={styles.status}><span className={styles.statusDot} /> STORAGE ONLINE <span className={styles.divider} /> LOCAL INSTANCE</div>
      </header>

      {toast && <div className={`${styles.toast} ${styles[`toast${toast.tone[0].toUpperCase()}${toast.tone.slice(1)}`]}`} role="status"><CheckCircle2 size={16} /><span>{toast.text}</span><button onClick={() => setToast(null)} aria-label="Cerrar notificación"><X size={14} /></button></div>}

      {view === "transactions" ? (
        <div className={styles.demoShell}>
          <section className={styles.demoHero}><div><span className={styles.eyebrow}>05 / TRANSACTION LAB</span><h1>¿Qué está pasando?</h1><p>Lee la transacción de arriba hacia abajo: cada punto representa un evento del motor y explica cómo cambian sus locks y su estado.</p></div><div className={styles.demoHeroStatus}><span className={styles.simulationBadge}><Zap size={14} /> {liveTimeline.length ? "DATOS REALES DEL WAL" : "GUION DE DEMO"}</span><span className={styles.liveBadge}>{liveStatus ? "BACKEND ONLINE" : "BACKEND OFFLINE"}</span></div></section>
          <div className={styles.demoGrid}>
            <aside className={styles.demoScenarios}><span className={styles.eyebrow}>ESCENARIOS</span>{Object.entries(demoScenarios).map(([key, item]) => <button key={key} className={key === demoScenario ? styles.scenarioActive : styles.scenario} onClick={() => chooseDemo(key)}><span>{item.title}</span><small>{item.runnable ? "ejecutable" : "requiere preparación"}</small></button>)}<button className={styles.runDemoButton} onClick={runDemo} disabled={demoRunning}><Play size={13} /> {demoRunning ? "EJECUTANDO SQL..." : demo.runnable ? "EJECUTAR DEMO REAL" : "VER REQUISITOS DE DEMO"}</button><div className={styles.liveTransactions}><span className={styles.eyebrow}>LIVE TRANSACTIONS</span>{liveStatus?.transactions.length ? liveStatus.transactions.slice(-4).map((transaction) => <button className={styles.liveTransactionButton} key={transaction.transaction_id} onClick={() => setSelectedTransactionId(transaction.transaction_id)}><b>TX-{transaction.transaction_id}</b><span>{transaction.status}</span></button>) : <small>Sin transacciones activas</small>}</div><div className={styles.demoControls}><button onClick={() => setDemoStep((step) => Math.max(0, step - 1))} disabled={activeDemoStep === 0}>PREV</button><button onClick={() => setDemoPlaying((playing) => !playing)}>{demoPlaying ? "PAUSAR" : "REPRODUCIR"}</button><button onClick={() => { setDemoStep(0); setDemoPlaying(false); }}><RotateCcw size={13} /> RESET</button><button onClick={() => setDemoStep((step) => Math.min(demo.events.length - 1, step + 1))} disabled={activeDemoStep === demo.events.length - 1}>NEXT</button></div></aside>
            <section className={styles.demoTimeline}><div className={styles.demoSectionHeader}><div><span className={styles.eyebrow}>PASO A PASO</span><h2>{selectedTimeline.length ? transactionDetail ? `TX-${transactionDetail.transaction_id}` : "Eventos recientes" : demo.title}</h2></div><span className={styles.stepCounter}>{selectedTimeline.length ? `${selectedTimeline.length} eventos` : `${String(activeDemoStep + 1).padStart(2, "0")} / ${String(demo.events.length).padStart(2, "0")}`}</span></div><div className={styles.timelineLegend}><span className={styles.liveDot} /> {selectedTimeline.length ? transactionDetail ? "EVENTOS DE LA TX SELECCIONADA" : "EVENTOS REALES DEL WAL" : "SIN EVENTOS REALES AÚN"}</div><div className={styles.eventList}>{selectedTimeline.length ? selectedTimeline.map((event) => <div className={`${styles.event} ${styles.eventVisible} ${styles.liveEvent}`} key={`${event.lsn}-${event.transaction_id}`}><span className={`${styles.eventMarker} ${styles.eventActive}`}>{event.type.slice(0, 3)}</span><div><strong>{eventExplanation[event.type] ?? event.type}</strong><p>TX-{event.transaction_id} · LSN {event.lsn} · {event.operation ?? "cambio de estado"}</p></div></div>) : demo.events.map((event, index) => <div className={`${styles.event} ${index <= activeDemoStep ? styles.eventVisible : ""} ${index === activeDemoStep ? styles.eventCurrent : ""}`} key={`${event.type}-${index}`}><span className={`${styles.eventMarker} ${styles[`event${event.tone[0].toUpperCase()}${event.tone.slice(1)}`]}`}>{index + 1}</span><div><strong>{event.label}</strong><p>{event.type} · {event.detail} · GUION, NO EVENTO REAL</p></div></div>)}</div><div className={styles.explainBox}><strong>Cómo leerlo</strong><span>La transacción empieza en `BEGIN`, toma locks para trabajar y termina en `COMMIT` o `ROLLBACK`. Selecciona una TX para aislar sus eventos.</span></div><pre className={styles.demoSql}>{demo.sql}</pre></section>
            <aside className={styles.demoInspector}><div className={styles.inspectorTitle}><span className={styles.eyebrow}>RESUMEN</span><strong>{transactionDetail ? `TX-${transactionDetail.transaction_id}` : "Sin TX seleccionada"}</strong></div><div className={styles.demoCard}><span className={styles.eyebrow}>LOCK ACTUAL</span><div className={styles.lockRow}><b>{transactionDetail?.locks[0]?.resource ?? liveStatus?.locks[0]?.resource ?? "Ninguno"}</b><span className={styles.lockMode}>{transactionDetail?.locks[0]?.mode ?? liveStatus?.locks[0]?.mode ?? "-"}</span></div><small>{transactionDetail ? `Estado: ${transactionDetail.status}` : "Selecciona una TX real"}</small></div><div className={styles.demoCard}><span className={styles.eyebrow}>ÚLTIMO WAL</span><div className={styles.walLine}><b>LSN {transactionDetail?.last_lsn ?? liveStatus?.wal.at(-1)?.lsn ?? "-"}</b><span>{transactionDetail?.events.at(-1)?.type ?? liveStatus?.wal.at(-1)?.type ?? "-"}</span></div><small>{transactionDetail ? `${transactionDetail.events.length} eventos de esta TX` : "Los eventos aparecen en la timeline"}</small></div><div className={styles.demoCard}><span className={styles.eyebrow}>RECOVERY</span><div className={styles.recoveryFacts}><b>{liveStatus?.recovery.clr_records ?? 0}</b><span>CLR generados</span><b>{liveStatus?.recovery.last_checkpoint ?? "-"}</b><span>último checkpoint</span></div></div></aside>
          </div>
        </div>
      ) : (
        <div className={styles.workspace}>
          <section className={`${styles.panel} ${styles.filesPanel}`}>
            <div className={styles.panelHeader}><div><span className={styles.eyebrow}>01 / CATALOG</span><h2 className={styles.panelTitle}>Archivos</h2></div><button className={styles.iconButton} onClick={loadTables} title="Actualizar catálogo"><RefreshCw size={15} /></button></div>
            <div className={styles.panelBody}><div className={styles.catalogHint}><FileBox size={15} /><span>{tables.length} tablas registradas</span></div><div className={styles.tableList}>{tables.map((table) => <div key={table.name} className={`${styles.tableItem} ${selected === table.name ? styles.selected : ""}`}><button onClick={() => setSelected(selected === table.name ? null : table.name)}><span className={styles.tableIcon}><Table2 size={15} /></span><span className={styles.tableName}>{table.name}</span>{selected === table.name ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>{selected === table.name && <div className={styles.columns}>{table.columns.map((column) => <div className={styles.column} key={column.name}><span>{column.primary_key ? "PK" : "  "}</span><b>{column.name}</b><small>{column.type}</small></div>)}<div className={styles.indexLabel}><Zap size={12} /> {table.indexes.length} índices</div></div>}</div>)}</div></div>
            {activeTable && <footer className={styles.panelFooter}><span>FILE TYPE</span><b>{activeTable.file_type.toUpperCase()}</b></footer>}
          </section>

          <section className={`${styles.panel} ${styles.planPanel}`}><div className={styles.panelHeader}><div><span className={styles.eyebrow}>02 / ANALYSIS</span><h2 className={styles.panelTitle}>Plan de ejecución</h2></div><span className={styles.costTag}>{result.plan.length ? `${result.plan.length} NODOS` : "IDLE"}</span></div><div className={styles.planBody}>{result.plan.length ? <div className={styles.planTree}>{result.plan.map((node, index) => <div className={styles.planNode} key={`${node.node}-${index}`}><div className={styles.nodeLine}><span className={styles.nodeIndex}>{String(index + 1).padStart(2, "0")}</span><strong>{node.node}</strong><span className={styles.nodeOp}>{node.operation}</span></div><div className={styles.nodeDetails}>{node.table && <span>TABLE <b>{node.table}</b></span>}{node.column && <span>COLUMN <b>{node.column}</b></span>}{node.index && <span>ACCESS <b>{node.index} / {node.access}</b></span>}{node.rows !== undefined && <span>ROWS <b>{node.rows}</b></span>}</div></div>)}</div> : <div className={styles.empty}><Zap size={25} /><p>El plan aparecerá después de ejecutar una consulta.</p></div>}</div></section>

          <section className={`${styles.panel} ${styles.queryPanel}`}><div className={styles.panelHeader}><div><span className={styles.eyebrow}>03 / WORKSPACE</span><h2 className={styles.panelTitle}>Consultas</h2></div><div className={styles.queryActions}><button className={styles.iconButton} onClick={copySql} title="Copiar SQL"><Copy size={14} /></button>{loading && <button className={styles.stopButton} onClick={cancelQuery}><CircleStop size={14} /> PARAR</button>}<button className={styles.runButton} onClick={() => runQuery()} disabled={loading}><Play size={14} fill="currentColor" /> {loading ? "EJECUTANDO" : "EJECUTAR"}<kbd className={styles.shortcut}>⌘ ↵</kbd></button></div></div><div className={styles.editorWrap}><div className={styles.editorGutter} ref={gutterRef}>{sql.split("\n").map((_, index) => <span key={index}>{String(index + 1).padStart(2, "0")}</span>)}</div><textarea ref={editorRef} className={styles.editorInput} aria-label="Editor de consulta SQL" spellCheck={false} value={sql} onChange={(event) => setSql(event.target.value)} onScroll={syncEditorScroll} onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") runQuery(); }} /></div><div className={styles.queryMeta}><span><Terminal size={13} /> SQL / ANSI-LIKE</span><span>{sql.length} caracteres {duration !== null && `· ${duration.toFixed(0)} ms`}</span></div>{error && <div className={styles.error}><AlertCircle size={15} /><span>{error}</span></div>}{history.length > 0 && <div className={styles.historyStrip}><History size={13} /><span>HISTORIAL</span>{history.slice(0, 3).map((item, index) => <button key={`${item.at}-${index}`} onClick={() => setSql(item.sql)} className={item.status === "error" ? styles.historyError : ""}>{item.at} · {item.rows} filas</button>)}</div>}</section>

          <section className={`${styles.panel} ${styles.resultsPanel}`}><div className={styles.panelHeader}><div><span className={styles.eyebrow}>04 / OUTPUT</span><h2 className={styles.panelTitle}>Resultados <em className={styles.resultCount}>{result.row_count ? `${result.row_count} filas` : ""}</em></h2></div><div className={styles.searchBadge}><Search size={14} /> <span>{result.columns.length ? "QUERY COMPLETE" : "WAITING"}</span></div></div><div className={styles.resultTableWrap}>{result.columns.length ? <table className={styles.resultTable}><thead><tr className={styles.resultRow}>{result.columns.map((column) => <th className={styles.resultHeader} key={column}>{column}</th>)}</tr></thead><tbody>{visibleRows.map((row, rowIndex) => <tr className={styles.resultRow} key={rowIndex}>{row.map((value, valueIndex) => <td className={styles.resultCell} key={valueIndex}>{String(value ?? "NULL")}</td>)}</tr>)}</tbody></table> : <div className={styles.empty}><Table2 size={25} /><p>{result.message}</p></div>}</div>{result.columns.length > 0 && <div className={styles.pagination}><span>ROWS {page * pageSize + 1}-{Math.min((page + 1) * pageSize, result.rows.length)} / {result.rows.length}</span><div><button disabled={page === 0} onClick={() => setPage(page - 1)}>PREV</button><b>{String(page + 1).padStart(2, "0")} / {String(pageCount).padStart(2, "0")}</b><button disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>NEXT</button></div></div>}</section>
          <section className={`${styles.panel} ${styles.spatialPanel}`}><div className={styles.panelHeader}><div><span className={styles.eyebrow}>05 / SPATIAL</span><h2 className={styles.panelTitle}>Mapa de puntos</h2></div><span className={styles.costTag}>{spatialPoints.length ? `${spatialPoints.length} PUNTOS` : "SIN DATOS"}</span></div><MapPanel points={spatialPoints} selectedTable={selected} /></section>
        </div>
      )}
    </main>
  );
}
