import { Card, Grid, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";

import { metricas } from "../api/servicos";

function Cartao({ titulo, valor, sub }: { titulo: string; valor: string | number; sub?: string }) {
  return (
    <Card withBorder>
      <Text size="xs" c="dimmed" tt="uppercase">
        {titulo}
      </Text>
      <Text fw={700} fz={28}>
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

/** Barras horizontais em SVG puro. Sem biblioteca de dashboard. */
function Barras({ dados, cor = "#1B8F5A" }: { dados: Record<string, number>; cor?: string }) {
  const entradas = Object.entries(dados).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entradas.map(([, v]) => v));
  const altura = 22;
  return (
    <svg width="100%" viewBox={`0 0 400 ${entradas.length * altura + 4}`} role="img">
      {entradas.map(([nome, valor], i) => (
        <g key={nome} transform={`translate(0, ${i * altura})`}>
          <text x={0} y={14} fontSize={11} fill="#5B6773">
            {nome}
          </text>
          <rect x={170} y={4} height={12} width={(valor / max) * 190} fill={cor} rx={2} />
          <text x={170 + (valor / max) * 190 + 6} y={14} fontSize={11} fill="#1B2430">
            {valor.toLocaleString("pt-BR")}
          </text>
        </g>
      ))}
    </svg>
  );
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

export function Metricas() {
  const q = useQuery({ queryKey: ["metricas", "qualidade"], queryFn: metricas.qualidade });
  const d = useQuery({ queryKey: ["metricas", "drift"], queryFn: metricas.drift });
  const m = q.data;

  return (
    <Stack>
      <Title order={3}>Métricas de operação</Title>
      {m && (
        <>
          <Grid>
            <Grid.Col span={{ base: 6, md: 2 }}>
              <Cartao titulo="Leituras" valor={m.total_leituras.toLocaleString("pt-BR")} />
            </Grid.Col>
            <Grid.Col span={{ base: 6, md: 2 }}>
              <Cartao titulo="Com foto" valor={m.com_foto.toLocaleString("pt-BR")} sub={`${m.sem_foto} sem foto`} />
            </Grid.Col>
            <Grid.Col span={{ base: 6, md: 2 }}>
              <Cartao
                titulo="Auto-decididas"
                valor={`${m.auto_decididas.percentual}%`}
                sub={`${m.auto_decididas.quantidade} leituras`}
              />
            </Grid.Col>
            <Grid.Col span={{ base: 6, md: 2 }}>
              <Cartao titulo="Fila humana" valor={m.fila_humana.pendentes.toLocaleString("pt-BR")} sub="pendentes" />
            </Grid.Col>
            <Grid.Col span={{ base: 6, md: 2 }}>
              <Cartao titulo="Decididas" valor={m.fila_humana.decididas.toLocaleString("pt-BR")} sub="por auditores" />
            </Grid.Col>
            <Grid.Col span={{ base: 6, md: 2 }}>
              <Cartao
                titulo="Tempo médio"
                valor={m.tempo_medio_decisao_segundos == null ? "—" : `${Math.round(m.tempo_medio_decisao_segundos)}s`}
                sub="fila → decisão"
              />
            </Grid.Col>
          </Grid>
          <Grid>
            <Grid.Col span={{ base: 12, md: 4 }}>
              <Card withBorder>
                <Title order={5}>Leituras por status</Title>
                <Barras dados={m.por_status} />
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 4 }}>
              <Card withBorder>
                <Title order={5}>Quarentena por tipo</Title>
                <Barras dados={m.quarentena_por_tipo} cor="#C9820A" />
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 4 }}>
              <Card withBorder>
                <Title order={5}>Motivos de reprovação</Title>
                {Object.keys(m.defeitos_qualidade).length ? (
                  <Barras dados={m.defeitos_qualidade} cor="#B3403A" />
                ) : (
                  <Text c="dimmed" size="sm">
                    nenhuma reprovação registrada
                  </Text>
                )}
              </Card>
            </Grid.Col>
          </Grid>
        </>
      )}

      <Card withBorder>
        <Group justify="space-between">
          <Title order={5}>Série por lote (drift)</Title>
          <Text size="xs" c="dimmed">
            sinais de modelo aparecem a partir do detector heurístico
          </Text>
        </Group>
        <Table mt="sm" striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Lote</Table.Th>
              <Table.Th>Extração</Table.Th>
              <Table.Th>Leituras</Table.Th>
              <Table.Th>Com foto</Table.Th>
              <Table.Th>Fila</Table.Th>
              <Table.Th>Auto-rejeição</Table.Th>
              <Table.Th>Reprovação humana</Table.Th>
              <Table.Th>Confiança média</Table.Th>
              <Table.Th>Modelos</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {d.data?.map((l) => (
              <Table.Tr key={l.lote}>
                <Table.Td ff="monospace">{l.lote.replace("PSP_EXTRATLEITIMPL_", "")}</Table.Td>
                <Table.Td>{l.data_extracao ? new Date(l.data_extracao).toLocaleDateString("pt-BR") : "—"}</Table.Td>
                <Table.Td>{l.total}</Table.Td>
                <Table.Td>{l.com_foto}</Table.Td>
                <Table.Td>{l.por_status.fila_humana ?? 0}</Table.Td>
                <Table.Td>{pct(l.taxa_auto_rejeicao)}</Table.Td>
                <Table.Td>{pct(l.taxa_reprovacao_humana)}</Table.Td>
                <Table.Td>{l.confianca_media == null ? "—" : l.confianca_media.toFixed(3)}</Table.Td>
                <Table.Td>{l.modelos.join(", ") || "—"}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
