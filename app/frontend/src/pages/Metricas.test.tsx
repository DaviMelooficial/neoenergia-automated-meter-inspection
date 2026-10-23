/**
 * Tela de metricas do M8: baseline, auto-aprovacao desligada com o motivo e o
 * contador dos 600, drift com alerta e auditoria. API mockada, valores ficticios.
 */
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AutoAprovacao,
  ConcordanciaAuditoria,
  LoteDrift,
  MetricasDrift,
  MetricasQualidade,
  SinalDrift,
} from "../api/servicos";

vi.mock("../api/servicos", async (importar) => {
  const real = await importar<typeof import("../api/servicos")>();
  return {
    ...real,
    metricas: { qualidade: vi.fn(), drift: vi.fn(), autoAprovacao: vi.fn(), auditoria: vi.fn() },
  };
});

import { metricas } from "../api/servicos";
import { Metricas } from "./Metricas";

const m = vi.mocked(metricas);
const LOTE_A = "PSP_EXTRATLEITIMPL_990526_0001";
const LOTE_B = "PSP_EXTRATLEITIMPL_990726_0001";

const decisoes = (com_foto: number, rej: number, fila: number, apr: number, rep: number) => ({
  com_foto,
  contagens: {
    auto_rejeitada: rej,
    auto_aprovada: 0,
    fila_humana: fila,
    aprovada_humano: apr,
    reprovada_humano: rep,
    em_processamento: 0,
  },
  taxas: {
    auto_rejeitada: rej / com_foto,
    auto_aprovada: 0,
    fila_humana: fila / com_foto,
    aprovada_humano: apr / com_foto,
    reprovada_humano: rep / com_foto,
    em_processamento: 0,
  },
});

const qualidade: MetricasQualidade = {
  periodo: { de: "2026-05-20", ate: "2026-07-03" },
  total_leituras: 120,
  com_foto: 100,
  sem_foto: 20,
  auto_decididas: { quantidade: 4, percentual: 4 },
  fila_humana: { pendentes: 90, decididas: 6 },
  por_status: { fila_humana: 90, rejeitada_qualidade: 4 },
  quarentena_por_tipo: { foto_orfa: 3 },
  defeitos_qualidade: { glare: 2 },
  revisoes_por_decisao: { aprovada: 4, reprovada: 2 },
  produtividade_por_auditor: [],
  tempo_medio_decisao_segundos: 12,
  decididas_conta_teste: 55,
  jobs: {},
  baseline: { lotes: [LOTE_A, LOTE_B], ...decisoes(100, 4, 90, 4, 2) },
  por_lote: [
    { lote: LOTE_A, data_extracao: "2026-05-20", ...decisoes(50, 1, 45, 2, 2) },
    { lote: LOTE_B, data_extracao: "2026-07-03", ...decisoes(50, 3, 45, 2, 0) },
  ],
  por_detector: [{ detector: "heuristica@v1", politica: "politica@v1", ...decisoes(100, 4, 90, 4, 2) }],
  concordancia_maquina_humano: [
    {
      fonte: "producao",
      escopo: "decisoes de auditor (fora da amostra)",
      politica: "politica@v1",
      n: 6,
      matriz: { rejeita_ilegivel: 0, rejeita_legivel: 0, passa_ilegivel: 2, passa_legivel: 4 },
      concordancia: 4 / 6,
      kappa: 0,
    },
  ],
};

const sinal = (valor: number, baseline: number, alerta = false): SinalDrift => ({
  valor,
  baseline,
  desvio: valor - baseline,
  alerta,
  n: 50,
  z: null,
});

function lote(nome: string, na_baseline: boolean, rej: number, alertas: string[]): LoteDrift {
  return {
    lote_id: na_baseline ? 1 : 2,
    lote: nome,
    data_extracao: null,
    na_baseline,
    total: 60,
    com_foto: 50,
    por_status: {},
    quarentena_por_tipo: {},
    modelos: ["heuristica@v1"],
    taxa_reprovacao_humana: null,
    n_fotos_com_score: 50,
    amostra_insuficiente: false,
    p_ruim_medio: 0.1,
    sinais: {
      classes: { fora_de_foco: sinal(0.05, 0.05), glare: sinal(0, 0) },
      taxa_auto_rejeicao: sinal(rej, 0.02, alertas.length > 0),
      confianca_media: sinal(0.9, 0.9),
      discordancia: sinal(0.1, 0.1),
      zona_cinzenta: sinal(0.04, 0.04),
    },
    alertas,
  };
}

const drift: MetricasDrift = {
  politica: "politica@v1",
  detector: "heuristica@v1",
  limiar_rejeita: 0.85,
  criterio: {
    min_fotos: 100,
    min_pares_discordancia: 30,
    classe_ativa: 0.5,
    inicio_zona_cinzenta: 0.5,
    desvio_classe: 0.1,
    desvio_rejeicao: 0.1,
    lotes_seguidos_rejeicao: 3,
    queda_confianca: 0.05,
    lotes_seguidos_confianca: 2,
    subida_discordancia: 0.1,
    subida_zona_cinzenta: 0.1,
    z_carta_p: 3,
    min_lotes_phi: 5,
  },
  baseline: {
    lotes: [LOTE_A],
    n_fotos_com_score: 50,
    n_pares_discordancia: 40,
    valores: {
      classes: { fora_de_foco: 0.05, glare: 0 },
      taxa_auto_rejeicao: 0.02,
      zona_cinzenta: 0.04,
      p_ruim_medio: 0.1,
      confianca_media: 0.9,
      discordancia: 0.1,
    },
    sobredispersao: { taxa_auto_rejeicao: 1 },
    sobredispersao_estimada: { taxa_auto_rejeicao: 4.9 },
  },
  lotes: [
    lote(LOTE_A, true, 0.02, []),
    lote(LOTE_B, false, 0.3, ["auto-rejeicao: +28.0 p.p. contra a baseline, 3 lotes seguidos"]),
  ],
};

const auto: AutoAprovacao = {
  politica: "politica@v1",
  detector: "heuristica@v1",
  limiar_rejeita: 0.85,
  limiar_aprova: null,
  ativa: false,
  contador_aplicavel: false,
  motivo:
    "Auto-aprovacao desligada: politica@v1 nao define limiar_aprova, e o detector heuristica@v1 nao tem zona de aprovacao com erro baixo.",
  alvo_erro: 0.005,
  confianca: 0.95,
  necessarias: 600,
  fotos_na_zona: null,
  rotuladas_na_zona: 0,
  erros: 0,
  acumuladas_sem_erro: 0,
  limite_superior_erro: null,
  meta_atingida: false,
  diagnostico: {
    limiar: 0.05,
    fonte: "lab02",
    n: 20,
    ilegiveis: 7,
    taxa_erro: 0.35,
    limite_inferior_erro: 0.18,
  },
};

const autoLigada: AutoAprovacao = {
  ...auto,
  politica: "politica@vX",
  limiar_aprova: 0.04,
  ativa: true,
  contador_aplicavel: true,
  motivo: "politica@vX define limiar_aprova = 0.04.",
  fotos_na_zona: 40,
  rotuladas_na_zona: 12,
  acumuladas_sem_erro: 12,
  limite_superior_erro: 0.2209,
};

const auditoria: ConcordanciaAuditoria = {
  rotulos_de_calibracao: { lab02: 500, a: 0, b: 0, desempate: 0, inspecao_m5: 0 },
  exclusao_incompleta: ["inspecao_m5"],
  taxa_configurada: 0.07,
  min_pares: 10,
  detalhe: true,
  auto_aprovadas: {
    detector: "heuristica@v1",
    politica: "politica@v1",
    etapa: "qualidade",
    n: 0,
    erros: 0,
    taxa_erro: null,
    limite_superior_erro: null,
    alvo_erro: 0.005,
    necessarias: 598,
    alvo_atingido: false,
    suprimido: false,
  },
  auto_rejeitadas: {
    detector: "heuristica@v1",
    politica: "politica@v1",
    etapa: "qualidade",
    n: 12,
    erros: 1,
    taxa_erro: 1 / 12,
    limite_superior_erro: 0.339,
    alvo_erro: 0.01,
    necessarias: 473,
    alvo_atingido: false,
    suprimido: false,
  },
  taxa_auto_rejeitada: 1,
  invalidadas: 0,
  estratos_auto_aprovadas: [],
  estratos_auto_rejeitadas: [],
  decisoes_na_fila: 100,
  sorteadas: 7,
  taxa_observada: 0.07,
  pendentes: 3,
  decididas: 4,
  total: {
    n: 4,
    concordancia_decisao: 0.75,
    kappa_decisao: 0.5,
    concordancia_motivo: 0.5,
    kappa_motivo: 1 / 3,
    suprimido: false,
  },
  por_lote: [],
  serie: [
    {
      dia: "2026-10-15",
      n: 2,
      concordancia_decisao: 1,
      kappa_decisao: null,
      concordancia_motivo: 1,
      kappa_motivo: null,
    },
    {
      dia: "2026-10-16",
      n: 4,
      concordancia_decisao: 0.75,
      kappa_decisao: 0.5,
      concordancia_motivo: 0.5,
      kappa_motivo: 1 / 3,
    },
  ],
};

function renderizar(esquema: "light" | "dark" = "light") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider forceColorScheme={esquema}>
      <QueryClientProvider client={client}>
        <Metricas />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("Metricas", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    m.qualidade.mockResolvedValue(qualidade);
    m.drift.mockResolvedValue(drift);
    m.autoAprovacao.mockResolvedValue(auto);
    m.auditoria.mockResolvedValue(auditoria);
  });

  it("explica a auto-aprovacao desligada, sem limiar candidato, e o contador contra os 600", async () => {
    renderizar();
    const painel = await screen.findByTestId("auto-aprovacao");
    expect(within(painel).getByText("auto-aprovação desligada")).toBeInTheDocument();
    expect(within(painel).getByText(/Por que a auto-aprovação está desligada/)).toBeInTheDocument();
    expect(within(painel).getByText(auto.motivo)).toBeInTheDocument();
    expect(within(painel).getByTestId("contador-600")).toHaveTextContent("0 de 600");
    expect(within(painel).getByTestId("limite-superior")).toHaveTextContent("—");
    expect(within(painel).getByText(/não se aplica a esta política/)).toBeInTheDocument();
    expect(within(painel).getByTestId("diagnostico-zona")).toHaveTextContent(
      "7 das 20 fotos com p_ruim ≤ 0,05 são ilegíveis (35,0%",
    );
    expect(painel).not.toHaveTextContent(/candidato do M8/);
    expect(within(painel).getByText("p_ruim ≥ 0,85")).toBeInTheDocument();
  });

  it("com limiar_aprova, conta e mostra o limite de Clopper-Pearson ao lado", async () => {
    m.autoAprovacao.mockResolvedValue(autoLigada);
    renderizar();
    const painel = await screen.findByTestId("auto-aprovacao");
    expect(within(painel).getByTestId("contador-600")).toHaveTextContent("12 de 600");
    expect(within(painel).getByTestId("limite-superior")).toHaveTextContent("22,09%");
    expect(within(painel).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "2");
    expect(within(painel).getByText(/fica em 0 para este detector e limiar/)).toBeInTheDocument();
  });

  it("mostra a baseline dos lotes e as taxas por lote", async () => {
    renderizar();
    const tabela = await screen.findByTestId("tabela-baseline");
    const linha = within(tabela).getByText("Baseline (2 lotes)").closest("tr")!;
    expect(linha).toHaveTextContent("4,00%");
    expect(within(tabela).getByText("990526")).toBeInTheDocument();
    expect(screen.getByText("auditores (produção)")).toBeInTheDocument();
    expect(screen.getByText("decisoes de auditor (fora da amostra)")).toBeInTheDocument();
    expect(screen.getByTestId("contas-teste")).toHaveTextContent("55 leituras decididas por contas de teste");
  });

  it("drift: alerta com icone e rotulo, nunca so a cor", async () => {
    renderizar();
    const tabela = await screen.findByTestId("tabela-drift");
    expect(within(tabela).getByText(/⚠ auto-rejeicao: \+28.0 p.p./)).toBeInTheDocument();
    expect(within(tabela).getByText("✓ nenhum")).toBeInTheDocument();
    const grafico = screen.getByRole("img", { name: "Taxa de auto-rejeição" });
    const alerta = grafico.querySelector("[data-alerta]")!;
    expect(alerta).toHaveTextContent("⚠");
    expect(alerta.querySelector("path")).toHaveAttribute("fill", "var(--viz-critico)");
  });

  it("drift da politica@v2: grafico da unica chave dela (ilegivel), sem as 5 classes", async () => {
    const soIlegivel = (l: LoteDrift): LoteDrift => ({
      ...l,
      sinais: { ...l.sinais, classes: { ilegivel: sinal(0.05, 0.04) } },
    });
    m.drift.mockResolvedValue({
      ...drift,
      politica: "politica@v2",
      detector: "qualidade@v1",
      limiar_rejeita: 0.976,
      lotes: drift.lotes.map(soIlegivel),
    });
    renderizar();
    await screen.findByTestId("tabela-drift");
    expect(screen.getByRole("img", { name: "Classe ativa: Ilegível" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Classe ativa: Fora de foco" })).toBeNull();
  });

  it("auditoria amostral: concordancia e kappa continuos", async () => {
    renderizar();
    const painel = await screen.findByTestId("painel-auditoria");
    const cartao = within(painel).getByText("Concordância").parentElement!;
    expect(cartao).toHaveTextContent("75,0%");
    expect(within(painel).getByText("0,500")).toBeInTheDocument();
    expect(within(painel).getByTestId("exclusao-incompleta")).toHaveTextContent("inspecao_m5");
    expect(within(painel).getByTestId("auditoria-auto")).toHaveTextContent(
      "12 auditadas, falso positivo 8,3% (até 33,9% com 95%). Alvo (precisão ≥ 99%) ainda não " +
        "sustentado: são precisas 473 auditorias com 1 erro(s).",
    );
    expect(within(painel).getByRole("img", { name: "concordância acumulada por dia" })).toBeInTheDocument();
  });

  it("10 auto-rejeicoes sem erro: mostra o limite de 25,9% e nao declara o alvo batido", async () => {
    m.auditoria.mockResolvedValue({
      ...auditoria,
      auto_rejeitadas: {
        detector: "heuristica@v1",
        politica: "politica@v1",
        etapa: "qualidade",
        n: 10,
        erros: 0,
        taxa_erro: 0,
        limite_superior_erro: 0.2589,
        alvo_erro: 0.01,
        necessarias: 299,
        alvo_atingido: false,
        suprimido: false,
      },
    });
    renderizar();
    const painel = await screen.findByTestId("painel-auditoria");
    const auto = within(painel).getByTestId("auditoria-auto");
    expect(auto).toHaveTextContent("falso positivo 0,0% (até 25,9% com 95%)");
    expect(auto).toHaveTextContent("ainda não sustentado: são precisas 299 auditorias");
    expect(auto).not.toHaveTextContent("sustentado com 95%");
  });

  it("auditoria para supervisor: sem contagens e com a medida oculta abaixo do minimo", async () => {
    m.auditoria.mockResolvedValue({
      ...auditoria,
      detalhe: false,
      decisoes_na_fila: null,
      sorteadas: null,
      taxa_observada: null,
      pendentes: null,
      decididas: null,
      total: {
        n: null,
        concordancia_decisao: null,
        kappa_decisao: null,
        concordancia_motivo: null,
        kappa_motivo: null,
        suprimido: true,
      },
      auto_aprovadas: {
        detector: "heuristica@v1",
        politica: "politica@v1",
        etapa: "qualidade",
        n: null,
        erros: null,
        taxa_erro: null,
        limite_superior_erro: null,
        alvo_erro: 0.01,
        necessarias: null,
        alvo_atingido: null,
        suprimido: true,
      },
      auto_rejeitadas: {
        detector: "heuristica@v1",
        politica: "politica@v1",
        etapa: "qualidade",
        n: null,
        erros: null,
        taxa_erro: null,
        limite_superior_erro: null,
        alvo_erro: 0.01,
        necessarias: null,
        alvo_atingido: null,
        suprimido: true,
      },
      invalidadas: null,
      por_lote: [],
      serie: [],
    });
    renderizar();
    const painel = await screen.findByTestId("painel-auditoria");
    expect(within(painel).queryByText("Sorteadas")).not.toBeInTheDocument();
    expect(within(painel).getByTestId("auditoria-suprimida")).toHaveTextContent("Menos de 10");
    expect(within(painel).getByTestId("auditoria-auto")).toHaveTextContent("oculto");
    expect(within(painel).queryByRole("img")).not.toBeInTheDocument();
  });

  it.each(["light", "dark"] as const)("graficos usam so tokens de tema (%s)", async (esquema) => {
    renderizar(esquema);
    await screen.findByTestId("tabela-drift");
    expect(document.documentElement).toHaveAttribute("data-mantine-color-scheme", esquema);
    const cores = [...document.querySelectorAll("svg.viz [fill], svg.viz [stroke]")].flatMap((el) =>
      ["fill", "stroke"].map((a) => el.getAttribute(a)).filter((v): v is string => v !== null),
    );
    expect(cores.length).toBeGreaterThan(0);
    expect(cores.every((c) => c.startsWith("var(--viz-") || c === "transparent" || c === "none")).toBe(true);
  });

  it("erro de carga aparece", async () => {
    m.drift.mockRejectedValue(new Error("x"));
    renderizar();
    expect(await screen.findByText(/Não foi possível carregar parte das métricas/)).toBeInTheDocument();
  });
});
