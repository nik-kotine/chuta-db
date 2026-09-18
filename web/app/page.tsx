"use client";

import { useEffect, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight, Database, FileBox, Play, RefreshCw, Search, Table2, Terminal, Zap } from "lucide-react";
import styles from "./page.module.css";

type Column = { name: string; type: string; position: number; primary_key: boolean };
type TableInfo = { name: string; file_type: string; key_index: number; columns: Column[]; indexes: { index_name: string; index_type: string; column_name: string }[] };
type PlanNode = { node: string; table?: string; index?: string; operation?: string; access?: string; column?: string; rows?: number };
type QueryResult = { message: string; columns: string[]; rows: unknown[][]; plan: PlanNode[]; row_count: number };

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const starterSql = "SELECT *\nFROM ventas\nWHERE id BETWEEN 1 AND 10\nORDER BY id ASC\nLIMIT 25;";

export default function Home() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [sql, setSql] = useState(starterSql);
  const [result, setResult] = useState<QueryResult>({ message: "Ejecuta una consulta para ver sus resultados.", columns: [], rows: [], plan: [], row_count: 0 });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
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

  async function runQuery() {
    setLoading(true);
    setError("");
    setPage(0);
    try {
      const response = await fetch(`${API}/api/query`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sql }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "La consulta no pudo ejecutarse");
      setResult(data);
      loadTables();
    } catch (queryError) {
      setError(queryError instanceof Error ? queryError.message : "Error de consulta");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadTables(); }, []);
  const activeTable = tables.find((table) => table.name === selected);
  const visibleRows = result.rows.slice(page * pageSize, (page + 1) * pageSize);
  const pageCount = Math.max(1, Math.ceil(result.rows.length / pageSize));

  return (
    <main className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.brand}><div className={styles.brandMark}><Database size={19} /></div><div><strong>CHUTA DB</strong><span>QUERY TERMINAL / 01</span></div></div>
        <div className={styles.status}><span className={styles.statusDot} /> STORAGE ONLINE <span className={styles.divider} /> LOCAL INSTANCE</div>
      </header>
      <div className={styles.workspace}>
        <section className={`${styles.panel} ${styles.filesPanel}`}>
          <div className={styles.panelHeader}><div><span className={styles.eyebrow}>01 / CATALOG</span><h2 className={styles.panelTitle}>Archivos</h2></div><button className={styles.iconButton} onClick={loadTables} title="Actualizar catálogo"><RefreshCw size={15} /></button></div>
          <div className={styles.panelBody}>
            <div className={styles.catalogHint}><FileBox size={15} /><span>{tables.length} tablas registradas</span></div>
            <div className={styles.tableList}>{tables.map((table) => <div key={table.name} className={`${styles.tableItem} ${selected === table.name ? styles.selected : ""}`}><button onClick={() => setSelected(selected === table.name ? null : table.name)}><span className={styles.tableIcon}><Table2 size={15} /></span><span className={styles.tableName}>{table.name}</span>{selected === table.name ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>{selected === table.name && <div className={styles.columns}>{table.columns.map((column) => <div className={styles.column} key={column.name}><span>{column.primary_key ? "PK" : "  "}</span><b>{column.name}</b><small>{column.type}</small></div>)}<div className={styles.indexLabel}><Zap size={12} /> {table.indexes.length} índices</div></div>}</div>)}</div>
          </div>
          {activeTable && <footer className={styles.panelFooter}><span>FILE TYPE</span><b>{activeTable.file_type.toUpperCase()}</b></footer>}
        </section>

        <section className={`${styles.panel} ${styles.queryPanel}`}>
          <div className={styles.panelHeader}><div><span className={styles.eyebrow}>02 / WORKSPACE</span><h2 className={styles.panelTitle}>Consultas</h2></div><button className={styles.runButton} onClick={runQuery} disabled={loading}><Play size={14} fill="currentColor" /> {loading ? "EJECUTANDO" : "EJECUTAR"}<kbd className={styles.shortcut}>⌘ ↵</kbd></button></div>
          <div className={styles.editorWrap}><div className={styles.editorGutter}>{sql.split("\n").map((_, index) => <span key={index}>{String(index + 1).padStart(2, "0")}</span>)}</div><textarea className={styles.editorInput} aria-label="Editor de consulta SQL" spellCheck={false} value={sql} onChange={(event) => setSql(event.target.value)} onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") runQuery(); }} /></div>
          <div className={styles.queryMeta}><span><Terminal size={13} /> SQL / ANSI-LIKE</span><span>{sql.length} caracteres</span></div>
          {error && <div className={styles.error}><AlertCircle size={15} /><span>{error}</span></div>}
        </section>

        <section className={`${styles.panel} ${styles.resultsPanel}`}>
          <div className={styles.panelHeader}><div><span className={styles.eyebrow}>03 / OUTPUT</span><h2 className={styles.panelTitle}>Resultados <em className={styles.resultCount}>{result.row_count ? `${result.row_count} filas` : ""}</em></h2></div><div className={styles.searchBadge}><Search size={14} /> <span>{result.columns.length ? "QUERY COMPLETE" : "WAITING"}</span></div></div>
          <div className={styles.resultTableWrap}>{result.columns.length ? <table className={styles.resultTable}><thead><tr className={styles.resultRow}>{result.columns.map((column) => <th className={styles.resultHeader} key={column}>{column}</th>)}</tr></thead><tbody>{visibleRows.map((row, rowIndex) => <tr className={styles.resultRow} key={rowIndex}>{row.map((value, valueIndex) => <td className={styles.resultCell} key={valueIndex}>{String(value ?? "NULL")}</td>)}</tr>)}</tbody></table> : <div className={styles.empty}><Table2 size={25} /><p>{result.message}</p></div>}</div>
          {result.columns.length > 0 && <div className={styles.pagination}><span>ROWS {page * pageSize + 1}-{Math.min((page + 1) * pageSize, result.rows.length)} / {result.rows.length}</span><div><button disabled={page === 0} onClick={() => setPage(page - 1)}>PREV</button><b>{String(page + 1).padStart(2, "0")} / {String(pageCount).padStart(2, "0")}</b><button disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>NEXT</button></div></div>}
        </section>

        <section className={`${styles.panel} ${styles.planPanel}`}>
          <div className={styles.panelHeader}><div><span className={styles.eyebrow}>04 / ANALYSIS</span><h2 className={styles.panelTitle}>Plan de ejecución</h2></div><span className={styles.costTag}>{result.plan.length ? `${result.plan.length} NODOS` : "IDLE"}</span></div>
          <div className={styles.planBody}>{result.plan.length ? <div className={styles.planTree}>{result.plan.map((node, index) => <div className={styles.planNode} key={`${node.node}-${index}`}><div className={styles.nodeLine}><span className={styles.nodeIndex}>{String(index + 1).padStart(2, "0")}</span><strong>{node.node}</strong><span className={styles.nodeOp}>{node.operation}</span></div><div className={styles.nodeDetails}>{node.table && <span>TABLE <b>{node.table}</b></span>}{node.column && <span>COLUMN <b>{node.column}</b></span>}{node.index && <span>ACCESS <b>{node.index} / {node.access}</b></span>}{node.rows !== undefined && <span>ROWS <b>{node.rows}</b></span>}</div></div>)}</div> : <div className={styles.empty}><Zap size={25} /><p>El plan aparecerá después de ejecutar una consulta.</p></div>}</div>
        </section>
      </div>
    </main>
  );
}
