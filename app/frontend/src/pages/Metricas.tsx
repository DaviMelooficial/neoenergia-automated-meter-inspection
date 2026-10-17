import {
  Alert,
  Badge,
  Card,
  Grid,
  Group,
  Progress,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";

import {
  metricas,
  type AutoAprovacao,
  type ConcordanciaAuditoria,
  type LoteDrift,
  type MetricasDrift,
  type MetricasQualidade,
  type SinalDrift,
} from "../api/servicos";
import { BarrasHorizontais, LinhaAcumulada, pct, SerieLotes } from "../components/Graficos";

const BUCKETS = [
  ["auto_rejeitada", "Auto-rejeitada"],
  ["fila_humana", "Fila humana"],
  ["aprovada_humano", "Aprovada (humano)"],
  ["reprovada_humano", "Reprovada (humano)"],
  ["auto_aprovada", "Auto-aprovada"],
  ["em_processamento", "Em processamento"],
] as const;

const CLASSES: [string, string][] = [
  ["fora_de_foco", "Fora de foco"],
  ["glare", "Reflexo"],
  ["tampa_suja", "Tampa suja"],
  ["enquadramento_incorreto", "Enquadramento"],
  ["display_apagado", "Display apagado"],
];

export const loteCurto = (nome: string) => nome.replace("PSP_EXTRATLEITIMPL_", "").split("_")[0];
const num = (v: number) => v.toLocaleString("pt-BR");
const dec = (v: number | null | undefined, casas = 2) =>
  v == null ? "—" : v.toFixed(casas).replace(".", ",");

function Cartao({ titulo, valor, sub }: { titulo: string; valor: string | number; sub?: string }) {
  return (
    <Card withBorder>
      <Text size="xs" c="dimmed">
        {titulo}
      </Text>
      <Text fw={600} fz={26}>
        {valor}
      </Text>
      {sub && (
        <Text size="xs" c="dimmed">
          {sub}
        </Text>
      )}
    </Card>
  );
}

/** T-M8-05: por que a auto-aprovacao esta desligada e quanta evidencia ja existe. */
export function PainelAutoAprovacao({ e }: { e: AutoAprovacao }) {
  const progresso = Math.min(100, (100 * e.acumuladas_sem_erro) / e.necessarias);
  return (
    <Card withBorder data-testid="auto-aprovacao">
      <Group justify="space-between" mb="xs">
        <Title order={5}>Limiares da política {e.politica}</Title>
        <Badge color={e.ativa ? "green" : "gray"} variant="light">
          auto-aprovação {e.ativa ? "ligada" : "desligada"}
        </Badge>
      </Group>
      <Table withRowBorders={false} verticalSpacing={2} mb="sm">
        <Table.Tbody>
          <Table.Tr>
            <Table.Td c="dimmed">Detector</Table.Td>
            <Table.Td ff="monospace">{e.detector ?? "nenhum"}</Table.Td>
          </Table.Tr>
          <Table.Tr>
            <Table.Td c="dimmed">limiar_rejeita</Table.Td>
            <Table.Td>
              {e.limiar_rejeita == null ? "desligado" : `p_ruim ≥ ${dec(e.limiar_rejeita)}`}
            </Table.Td>
          </Table.Tr>
          <Table.Tr>
            <Table.Td c="dimmed">limiar_aprova</Table.Td>
            <Table.Td>{e.limiar_aprova == null ? "desligado" : `p_ruim ≤ ${dec(e.limiar_aprova)}`}</Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>
      {!e.ativa && (
        <Alert color="yellow" title="ⓘ Por que a auto-aprovação está desligada" mb="sm">
          <Text size="sm">{e.motivo}</Text>
        </Alert>
      )}
      <Group justify="space-between" mb={4}>
        <Text size="sm" fw={600}>
          Rótulos acumulados na zona de aprovação
        </Text>
        <Text size="sm" data-testid="contador-600">
          <b>{num(e.acumuladas_sem_erro)}</b> de {num(e.necessarias)}
        </Text>
      </Group>
      <Progress value={progresso} aria-label="rótulos acumulados contra os necessários" />
      <Text size="xs" c="dimmed" mt="xs">
        Conta as fotos com p_ruim ≤ {dec(e.limiar_contagem)} (
        {e.origem_limiar_contagem === "politica"
          ? "limiar da política"
          : "limiar candidato do M8, só para contagem"}
        ) que têm decisão de auditor com legibilidade definida: {num(e.rotuladas_na_zona)} de{" "}
        {num(e.fotos_na_zona)} fotos na zona. Erro é uma delas julgada ilegível pelo auditor; com qualquer
        erro, o contador volta a zero.
        {e.erros > 0 && ` Erros encontrados: ${e.erros}.`}
        {e.limite_superior_erro != null &&
          ` Hoje o erro máximo demonstrável (95%) é ${pct(e.limite_superior_erro, 2)}; o alvo é ${pct(e.alvo_erro)}.`}
      </Text>
    </Card>
  );
}

function TabelaDecisoes({ q }: { q: MetricasQualidade }) {
  const linhas = [
    ...q.por_lote.map((l) => ({ chave: l.lote, nome: loteCurto(l.lote), d: l, forte: false })),
    { chave: "baseline", nome: `Baseline (${q.baseline.lotes.length} lotes)`, d: q.baseline, forte: true },
  ];
  return (
    <Table striped mt="sm" data-testid="tabela-baseline">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Lote</Table.Th>
          <Table.Th>Com foto</Table.Th>
          {BUCKETS.slice(0, 4).map(([k, r]) => (
            <Table.Th key={k}>{r}</Table.Th>
          ))}
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {linhas.map(({ chave, nome, d, forte }) => (
          <Table.Tr key={chave} fw={forte ? 700 : undefined}>
            <Table.Td ff="monospace">{nome}</Table.Td>
            <Table.Td>{num(d.com_foto)}</Table.Td>
            {BUCKETS.slice(0, 4).map(([k]) => (
              <Table.Td key={k}>
                {pct(d.taxas[k], 2)}{" "}
                <Text span size="xs" c="dimmed">
                  ({num(d.contagens[k] ?? 0)})
                </Text>
              </Table.Td>
            ))}
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function Concordancia({ q }: { q: MetricasQualidade }) {
  return (
    <Table mt="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Humano</Table.Th>
          <Table.Th>Pares</Table.Th>
          <Table.Th>Rejeita × ilegível</Table.Th>
          <Table.Th>Rejeita × legível</Table.Th>
          <Table.Th>Passa × ilegível</Table.Th>
          <Table.Th>Passa × legível</Table.Th>
          <Table.Th>Concordância</Table.Th>
          <Table.Th>Kappa</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {q.concordancia_maquina_humano.map((c) => (
          <Table.Tr key={c.fonte}>
            <Table.Td>{c.fonte === "producao" ? "auditores (produção)" : "Lab 2 (amostra fechada)"}</Table.Td>
            <Table.Td>{num(c.n)}</Table.Td>
            <Table.Td>{c.matriz.rejeita_ilegivel}</Table.Td>
            <Table.Td>{c.matriz.rejeita_legivel}</Table.Td>
            <Table.Td>{c.matriz.passa_ilegivel}</Table.Td>
            <Table.Td>{c.matriz.passa_legivel}</Table.Td>
            <Table.Td>{pct(c.concordancia)}</Table.Td>
            <Table.Td>{dec(c.kappa, 3)}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function pontos(lotes: LoteDrift[], sinal: (l: LoteDrift) => SinalDrift) {
  return lotes.map((l) => {
    const s = sinal(l);
    return {
      rotulo: loteCurto(l.lote),
      valor: s.valor,
      alerta: s.alerta,
      naBaseline: l.na_baseline,
      detalhe:
        s.desvio == null
          ? undefined
          : `desvio ${s.desvio >= 0 ? "+" : ""}${(100 * s.desvio).toFixed(1)} p.p.`,
    };
  });
}

function Drift({ d }: { d: MetricasDrift }) {
  const base = d.baseline.valores;
  const multiplos: {
    titulo: string;
    sinal: (l: LoteDrift) => SinalDrift;
    base: number | null;
    fmt?: (v: number) => string;
  }[] = [
    {
      titulo: "Taxa de auto-rejeição",
      sinal: (l) => l.sinais.taxa_auto_rejeicao,
      base: base?.taxa_auto_rejeicao ?? null,
    },
    {
      titulo: "Confiança média",
      sinal: (l) => l.sinais.confianca_media,
      base: base?.confianca_media ?? null,
      fmt: (v) => dec(v, 3),
    },
    {
      titulo: "Discordância humano × modelo",
      sinal: (l) => l.sinais.discordancia,
      base: base?.discordancia ?? null,
    },
    {
      titulo: "Zona cinzenta (aparência nova)",
      sinal: (l) => l.sinais.zona_cinzenta,
      base: base?.zona_cinzenta ?? null,
    },
    ...CLASSES.filter(([c]) => d.lotes.every((l) => c in l.sinais.classes)).map(([c, r]) => ({
      titulo: `Classe ativa: ${r}`,
      sinal: (l: LoteDrift) => l.sinais.classes[c],
      base: base?.classes[c] ?? null,
    })),
  ];
  const c = d.criterio;
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        {d.politica} sobre {d.detector ?? "nenhum detector"}. Baseline:{" "}
        {d.baseline.lotes.map(loteCurto).join(", ") || "nenhum lote"} ({num(d.baseline.n_fotos_com_score)}{" "}
        fotos). Coluna clara = lote da baseline; ⚠ = alerta.
      </Text>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="sm">
        {multiplos.map((m) => (
          <Card withBorder key={m.titulo} padding="xs">
            <Text size="sm" fw={600}>
              {m.titulo}
            </Text>
            <SerieLotes
              titulo={m.titulo}
              pontos={pontos(d.lotes, m.sinal)}
              baseline={m.base}
              formato={m.fmt}
            />
          </Card>
        ))}
      </SimpleGrid>
      <Table striped data-testid="tabela-drift">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Lote</Table.Th>
            <Table.Th>Fotos com score</Table.Th>
            <Table.Th>Auto-rejeição</Table.Th>
            <Table.Th>Confiança</Table.Th>
            <Table.Th>Discordância (pares)</Table.Th>
            <Table.Th>Zona cinzenta</Table.Th>
            <Table.Th>Alertas</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {d.lotes.map((l) => (
            <Table.Tr key={l.lote}>
              <Table.Td ff="monospace">
                {loteCurto(l.lote)}{" "}
                {l.na_baseline && (
                  <Badge size="xs" variant="light">
                    baseline
                  </Badge>
                )}
              </Table.Td>
              <Table.Td>{num(l.n_fotos_com_score)}</Table.Td>
              <Table.Td>{pct(l.sinais.taxa_auto_rejeicao.valor, 2)}</Table.Td>
              <Table.Td>{dec(l.sinais.confianca_media.valor, 3)}</Table.Td>
              <Table.Td>
                {pct(l.sinais.discordancia.valor)} ({l.sinais.discordancia.n})
              </Table.Td>
              <Table.Td>{pct(l.sinais.zona_cinzenta.valor, 2)}</Table.Td>
              <Table.Td>
                {l.amostra_insuficiente ? (
                  <Text size="xs" c="dimmed">
                    amostra insuficiente
                  </Text>
                ) : l.alertas.length ? (
                  <Stack gap={0}>
                    {l.alertas.map((a) => (
                      <Text key={a} size="xs" c="red">
                        ⚠ {a}
                      </Text>
                    ))}
                  </Stack>
                ) : (
                  <Text size="xs" c="dimmed">
                    ✓ nenhum
                  </Text>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Text size="xs" c="dimmed">
        Critério de alerta (desvio contra a baseline): classe ativa (score ≥ {dec(c.classe_ativa, 1)}) com
        |desvio| &gt; {pct(c.desvio_classe, 0)}; auto-rejeição com |desvio| &gt; {pct(c.desvio_rejeicao, 0)}{" "}
        em {c.lotes_seguidos_rejeicao} lotes seguidos; confiança média caindo mais de {dec(c.queda_confianca)}{" "}
        em {c.lotes_seguidos_confianca} lotes seguidos; discordância subindo mais de{" "}
        {pct(c.subida_discordancia, 0)} (com ≥ {c.min_pares_discordancia} pares); zona cinzenta (
        {dec(c.inicio_zona_cinzenta, 1)} ≤ p_ruim &lt; limiar de rejeição) subindo mais de{" "}
        {pct(c.subida_zona_cinzenta, 0)}. Lote com menos de {c.min_fotos} fotos não alerta.
      </Text>
    </Stack>
  );
}

export function PainelAuditoria({ a }: { a: ConcordanciaAuditoria }) {
  return (
    <Stack gap="sm" data-testid="painel-auditoria">
      <SimpleGrid cols={{ base: 2, md: 6 }} spacing="sm">
        <Cartao
          titulo="Taxa configurada"
          valor={pct(a.taxa_configurada, 0)}
          sub={`observada ${pct(a.taxa_observada)}`}
        />
        <Cartao titulo="Sorteadas" valor={num(a.sorteadas)} sub={`de ${num(a.decisoes_na_fila)} decisões`} />
        <Cartao titulo="Pendentes" valor={num(a.pendentes)} />
        <Cartao titulo="Decididas" valor={num(a.decididas)} />
        <Cartao titulo="Concordância" valor={pct(a.total.concordancia_decisao)} sub="aprovar × reprovar" />
        <Cartao
          titulo="Kappa de Cohen"
          valor={dec(a.total.kappa_decisao, 3)}
          sub={`motivo: ${dec(a.total.kappa_motivo, 3)}`}
        />
      </SimpleGrid>
      {a.serie.length > 0 ? (
        <Card withBorder padding="xs">
          <Text size="sm" fw={600}>
            Concordância acumulada (decisão)
          </Text>
          <LinhaAcumulada
            titulo="concordância acumulada por dia"
            pontos={a.serie.map((p) => ({
              rotulo: new Date(`${p.dia}T12:00:00`).toLocaleDateString("pt-BR"),
              valor: p.concordancia_decisao,
              n: p.n,
            }))}
          />
        </Card>
      ) : (
        <Text size="sm" c="dimmed">
          Nenhuma auditoria decidida ainda: a concordância aparece quando o segundo revisor decidir.
        </Text>
      )}
      {a.por_lote.length > 0 && (
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Lote</Table.Th>
              <Table.Th>Auditorias</Table.Th>
              <Table.Th>Concordância (decisão)</Table.Th>
              <Table.Th>Kappa (decisão)</Table.Th>
              <Table.Th>Concordância (motivo)</Table.Th>
              <Table.Th>Kappa (motivo)</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {a.por_lote.map((l) => (
              <Table.Tr key={l.lote}>
                <Table.Td ff="monospace">{loteCurto(l.lote)}</Table.Td>
                <Table.Td>{l.n}</Table.Td>
                <Table.Td>{pct(l.concordancia_decisao)}</Table.Td>
                <Table.Td>{dec(l.kappa_decisao, 3)}</Table.Td>
                <Table.Td>{pct(l.concordancia_motivo)}</Table.Td>
                <Table.Td>{dec(l.kappa_motivo, 3)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

export function Metricas() {
  const q = useQuery({ queryKey: ["metricas", "qualidade"], queryFn: metricas.qualidade });
  const d = useQuery({ queryKey: ["metricas", "drift"], queryFn: metricas.drift });
  const e = useQuery({ queryKey: ["metricas", "auto-aprovacao"], queryFn: metricas.autoAprovacao });
  const a = useQuery({ queryKey: ["metricas", "auditoria"], queryFn: metricas.auditoria });
  const m = q.data;

  return (
    <Stack>
      <Title order={3}>Métricas de operação</Title>
      {(q.isError || d.isError || e.isError || a.isError) && (
        <Alert color="red" role="alert">
          Não foi possível carregar parte das métricas.
        </Alert>
      )}
      {e.data && <PainelAutoAprovacao e={e.data} />}
      {m && (
        <Card withBorder>
          <Title order={5}>Baseline sobre os lotes reais</Title>
          <Text size="xs" c="dimmed">
            Taxas sobre as leituras com foto, a medida que o processo anterior não tinha. Período{" "}
            {m.periodo.de ?? "—"} a {m.periodo.ate ?? "—"}.
          </Text>
          <SimpleGrid cols={{ base: 2, md: 5 }} spacing="sm" mt="sm">
            <Cartao
              titulo="Leituras com foto"
              valor={num(m.baseline.com_foto)}
              sub={`${num(m.sem_foto)} sem foto`}
            />
            {BUCKETS.slice(0, 4).map(([k, r]) => (
              <Cartao
                key={k}
                titulo={r}
                valor={pct(m.baseline.taxas[k], 2)}
                sub={`${num(m.baseline.contagens[k] ?? 0)} leituras`}
              />
            ))}
          </SimpleGrid>
          <TabelaDecisoes q={m} />
          <Title order={6} mt="md">
            Por detector e política
          </Title>
          <Table mt="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Detector</Table.Th>
                <Table.Th>Política</Table.Th>
                <Table.Th>Com foto</Table.Th>
                {BUCKETS.slice(0, 4).map(([k, r]) => (
                  <Table.Th key={k}>{r}</Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {m.por_detector.map((g) => (
                <Table.Tr key={`${g.detector}-${g.politica}`}>
                  <Table.Td ff="monospace">{g.detector}</Table.Td>
                  <Table.Td ff="monospace">{g.politica}</Table.Td>
                  <Table.Td>{num(g.com_foto)}</Table.Td>
                  {BUCKETS.slice(0, 4).map(([k]) => (
                    <Table.Td key={k}>{pct(g.taxas[k], 2)}</Table.Td>
                  ))}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Title order={6} mt="md">
            Concordância máquina × humano
          </Title>
          <Text size="xs" c="dimmed">
            Máquina diz ilegível quando p_ruim ≥ limiar de rejeição. Só fotos com score e rótulo humano.
          </Text>
          <Concordancia q={m} />
        </Card>
      )}

      <Card withBorder>
        <Title order={5} mb="xs">
          Drift por lote
        </Title>
        {d.data ? <Drift d={d.data} /> : <Text c="dimmed">carregando…</Text>}
      </Card>

      <Card withBorder>
        <Title order={5} mb="xs">
          Auditoria amostral (segundo revisor)
        </Title>
        {a.data ? <PainelAuditoria a={a.data} /> : <Text c="dimmed">carregando…</Text>}
      </Card>

      {m && (
        <Grid>
          <Grid.Col span={{ base: 12, md: 4 }}>
            <Card withBorder>
              <Title order={5}>Leituras por status</Title>
              <BarrasHorizontais dados={m.por_status} />
            </Card>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4 }}>
            <Card withBorder>
              <Title order={5}>Quarentena por tipo</Title>
              <BarrasHorizontais dados={m.quarentena_por_tipo} />
            </Card>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4 }}>
            <Card withBorder>
              <Title order={5}>Motivos de reprovação</Title>
              {Object.keys(m.defeitos_qualidade).length ? (
                <BarrasHorizontais dados={m.defeitos_qualidade} />
              ) : (
                <Text c="dimmed" size="sm">
                  nenhuma reprovação registrada
                </Text>
              )}
            </Card>
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  );
}
