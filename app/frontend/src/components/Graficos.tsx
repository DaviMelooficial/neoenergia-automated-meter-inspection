/**
 * Graficos em SVG puro, sem biblioteca de dashboard (doc 05, secao 5). Cores so por
 * token (`graficos.css`), para valer nos temas claro e escuro. Texto usa tinta, nunca
 * a cor da serie; alerta usa a cor critica sempre com icone e rotulo. Cada marca tem
 * <title> (tooltip nativo ao passar o mouse) e a tela traz a tabela com os numeros.
 */
import "./graficos.css";

const T = {
  tinta: "var(--viz-tinta)",
  tinta2: "var(--viz-tinta-2)",
  fraca: "var(--viz-tinta-fraca)",
  grade: "var(--viz-grade)",
  eixo: "var(--viz-eixo)",
  serie: "var(--viz-serie-1)",
  serieFraca: "var(--viz-serie-1-fraca)",
  critico: "var(--viz-critico)",
};

export const pct = (v: number | null | undefined, casas = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(casas).replace(".", ",")}%`;

/** Barra de topo arredondado (4px) e base reta, crescendo da linha de base. */
function coluna(x: number, y: number, w: number, h: number) {
  if (h <= 0) return "";
  const r = Math.min(4, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** Barras horizontais (contagens por categoria). Uma serie: sem legenda. */
export function BarrasHorizontais({ dados }: { dados: Record<string, number> }) {
  const entradas = Object.entries(dados).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entradas.map(([, v]) => v));
  const passo = 24;
  return (
    <svg
      className="viz"
      width="100%"
      viewBox={`0 0 400 ${entradas.length * passo + 4}`}
      role="img"
      aria-label="barras por categoria"
    >
      {entradas.map(([nome, valor], i) => {
        const w = (valor / max) * 170;
        return (
          <g key={nome} transform={`translate(0, ${i * passo})`} className="marca">
            <title>{`${nome}: ${valor.toLocaleString("pt-BR")}`}</title>
            <text x={0} y={15} fontSize={11} fill={T.tinta2}>
              {nome}
            </text>
            {w > 0 && (
              <path
                d={`M170,5H${170 + w - 4}Q${170 + w},5 ${170 + w},9V15Q${170 + w},19 ${170 + w - 4},19H170Z`}
                fill={T.serie}
              />
            )}
            <text x={170 + w + 6} y={15} fontSize={11} fill={T.tinta}>
              {valor.toLocaleString("pt-BR")}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export interface PontoLote {
  rotulo: string;
  valor: number | null;
  alerta: boolean;
  naBaseline: boolean;
  detalhe?: string;
}

/**
 * Um sinal de drift por lote: colunas com o valor, linha de referencia na baseline.
 * Lote da baseline em tom claro da mesma serie; lote em alerta na cor critica, com
 * o icone sobre a coluna. Feito para small multiples (um grafico por sinal).
 */
export function SerieLotes({
  titulo,
  pontos,
  baseline,
  formato = (v: number) => pct(v),
}: {
  titulo: string;
  pontos: PontoLote[];
  baseline: number | null;
  formato?: (v: number) => string;
}) {
  const L = 260;
  const A = 150;
  const topo = 22;
  const base = A - 30;
  const valores = pontos.map((p) => p.valor ?? 0);
  const max = Math.max(baseline ?? 0, ...valores, 1e-9) * 1.15;
  const y = (v: number) => base - (v / max) * (base - topo);
  const banda = (L - 20) / Math.max(1, pontos.length);
  const larg = Math.min(24, banda * 0.5);
  return (
    <div className="viz">
      {/* legenda da linha de referencia fora da area de plotagem: dentro dela colidia
          com o rotulo de valor da coluna mais alta */}
      <div style={{ fontSize: 11, color: T.tinta2, display: "flex", alignItems: "center", gap: 6 }}>
        <svg width="16" height="8" aria-hidden="true">
          <line x1={0} x2={16} y1={4} y2={4} stroke={T.fraca} strokeWidth={1} />
        </svg>
        {baseline == null ? "sem baseline" : `baseline ${formato(baseline)}`}
      </div>
      <svg className="viz" width="100%" viewBox={`0 0 ${L} ${A}`} role="img" aria-label={titulo}>
        <line x1={10} x2={L - 10} y1={base} y2={base} stroke={T.eixo} strokeWidth={1} />
        {baseline != null && (
          <line x1={10} x2={L - 10} y1={y(baseline)} y2={y(baseline)} stroke={T.fraca} strokeWidth={1} />
        )}
        {pontos.map((p, i) => {
          const cx = 10 + banda * i + banda / 2;
          const v = p.valor;
          const cor = p.alerta ? T.critico : p.naBaseline ? T.serieFraca : T.serie;
          return (
            <g key={p.rotulo} className="marca" data-alerta={p.alerta || undefined}>
              <title>
                {`${p.rotulo}: ${v == null ? "sem dado" : formato(v)}` +
                  (p.naBaseline ? " (baseline)" : "") +
                  (p.alerta ? " — ALERTA" : "") +
                  (p.detalhe ? ` · ${p.detalhe}` : "")}
              </title>
              {/* alvo de hover maior que a marca */}
              <rect
                x={cx - banda / 2}
                y={topo - 14}
                width={banda}
                height={base - topo + 14}
                fill="transparent"
              />
              {v != null && <path d={coluna(cx - larg / 2, y(v), larg, base - y(v))} fill={cor} />}
              <text x={cx} y={(v == null ? base : y(v)) - 5} fontSize={10} fill={T.tinta} textAnchor="middle">
                {p.alerta ? "⚠ " : ""}
                {v == null ? "—" : formato(v)}
              </text>
              <text x={cx} y={base + 14} fontSize={10} fill={T.tinta2} textAnchor="middle">
                {p.rotulo}
              </text>
              {p.naBaseline && (
                <text x={cx} y={base + 26} fontSize={9} fill={T.fraca} textAnchor="middle">
                  base
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Linha da concordancia acumulada (0 a 100%), um ponto por dia. */
export function LinhaAcumulada({
  pontos,
  titulo,
}: {
  pontos: { rotulo: string; valor: number | null; n: number }[];
  titulo: string;
}) {
  const L = 520;
  const A = 160;
  const esq = 36;
  const topo = 12;
  const base = A - 28;
  const y = (v: number) => base - v * (base - topo);
  const passo = pontos.length > 1 ? (L - esq - 16) / (pontos.length - 1) : 0;
  const x = (i: number) => (pontos.length > 1 ? esq + i * passo : (L + esq) / 2);
  const validos = pontos.map((p, i) => ({ ...p, i })).filter((p) => p.valor != null);
  const d = validos.map((p, k) => `${k ? "L" : "M"}${x(p.i)},${y(p.valor!)}`).join("");
  return (
    <svg className="viz" width="100%" viewBox={`0 0 ${L} ${A}`} role="img" aria-label={titulo}>
      {[0, 0.5, 1].map((t) => (
        <g key={t}>
          <line x1={esq} x2={L - 8} y1={y(t)} y2={y(t)} stroke={t === 0 ? T.eixo : T.grade} strokeWidth={1} />
          <text x={esq - 6} y={y(t) + 3} fontSize={10} fill={T.fraca} textAnchor="end">
            {pct(t, 0)}
          </text>
        </g>
      ))}
      <path d={d} fill="none" stroke={T.serie} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {validos.map((p) => (
        <g key={p.rotulo} className="marca">
          <title>{`${p.rotulo}: ${pct(p.valor)} em ${p.n} auditorias`}</title>
          <circle cx={x(p.i)} cy={y(p.valor!)} r={10} fill="transparent" />
          <circle
            cx={x(p.i)}
            cy={y(p.valor!)}
            r={4}
            fill={T.serie}
            stroke="var(--viz-superficie)"
            strokeWidth={2}
          />
        </g>
      ))}
      {validos.length > 0 && (
        <text
          x={x(validos.at(-1)!.i)}
          y={y(validos.at(-1)!.valor!) - 10}
          fontSize={11}
          fill={T.tinta}
          textAnchor="end"
        >
          {pct(validos.at(-1)!.valor)}
        </text>
      )}
      {pontos.map((p, i) =>
        i === 0 || i === pontos.length - 1 ? (
          <text key={p.rotulo} x={x(i)} y={base + 16} fontSize={10} fill={T.tinta2} textAnchor="middle">
            {p.rotulo}
          </text>
        ) : null,
      )}
    </svg>
  );
}
