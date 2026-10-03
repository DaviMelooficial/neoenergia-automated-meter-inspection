import { Badge, Group, Progress, Stack, Table, Text, Title } from "@mantine/core";

import type { LeituraOut } from "../api/servicos";

const CLASSES = ["fora_de_foco", "glare", "tampa_suja", "enquadramento_incorreto", "display_apagado"];

/** Dados declarados pelo leiturista, ao lado da foto, mais sinais do modelo. */
export function PainelLeitura({ leitura }: { leitura: LeituraOut }) {
  const ultimaInferencia = leitura.inferencias.at(-1);
  const scores = (ultimaInferencia?.saida ?? {}) as Record<string, number>;
  const motivoFila = leitura.historico.find((h) => h.status === "fila_humana")?.detalhe as
    | Record<string, unknown>
    | null
    | undefined;

  return (
    <Stack gap="md">
      <div>
        <Title order={4}>Dados declarados</Title>
        <Table withRowBorders={false} verticalSpacing={4} mt="xs">
          <Table.Tbody>
            <Table.Tr>
              <Table.Td c="dimmed">Medidor{leitura.medidores.length > 1 ? "es" : ""}</Table.Td>
              <Table.Td>
                <Group gap={6}>
                  {leitura.medidores.map((m) => (
                    <Badge key={m} variant="light" color="gray" size="lg" ff="monospace">
                      {m}
                    </Badge>
                  ))}
                  {leitura.medidores.length > 1 && (
                    <Badge color="orange" variant="outline">
                      múltiplos
                    </Badge>
                  )}
                </Group>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td c="dimmed">Leitura declarada</Table.Td>
              <Table.Td>
                <Text ff="monospace" fw={700} size="xl" data-testid="posicao-lida">
                  {leitura.posicao_lida ?? "—"}
                </Text>
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td c="dimmed">Nota de ocorrência</Table.Td>
              <Table.Td>
                {leitura.nota ? (
                  <Stack gap={2}>
                    <Group gap={6}>
                      <Badge color="blue">{leitura.nota.codigo}</Badge>
                      <Badge variant="light" color={leitura.nota.exige_foto ? "green" : "gray"}>
                        {leitura.nota.exige_foto ? "exige foto" : "não exige foto"}
                      </Badge>
                    </Group>
                    <Text size="sm">{leitura.nota.descricao}</Text>
                  </Stack>
                ) : leitura.nota_codigo ? (
                  <Badge color="red">{leitura.nota_codigo} · código não mapeado</Badge>
                ) : (
                  <Text c="dimmed">sem nota</Text>
                )}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td c="dimmed">Lote · linha</Table.Td>
              <Table.Td>
                <Text size="sm" ff="monospace">
                  {leitura.lote.replace("PSP_EXTRATLEITIMPL_", "")} · {leitura.linha_csv}
                </Text>
              </Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
      </div>

      {leitura.quarentena.length > 0 && (
        <div>
          <Title order={5}>Inconsistências</Title>
          <Group gap={6} mt={4}>
            {leitura.quarentena.map((q) => (
              <Badge
                key={q.id}
                color={q.severidade === "bloqueante" ? "red" : "yellow"}
                variant="light"
              >
                {q.tipo}
              </Badge>
            ))}
          </Group>
        </div>
      )}

      <div>
        <Title order={5}>Sinais do modelo</Title>
        {ultimaInferencia && Object.keys(scores).length > 0 ? (
          <Stack gap={6} mt={4}>
            {CLASSES.map((c) => (
              <div key={c}>
                <Group justify="space-between" gap={4}>
                  <Text size="xs">{c}</Text>
                  <Text size="xs" c="dimmed">
                    {((scores[c] ?? 0) * 100).toFixed(0)}%
                  </Text>
                </Group>
                <Progress value={(scores[c] ?? 0) * 100} size="sm" color="orange" />
              </div>
            ))}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed" mt={4}>
            {ultimaInferencia
              ? `${ultimaInferencia.modelo}: sem sinal de qualidade (detector nulo)`
              : "nenhuma inferência registrada"}
          </Text>
        )}
        {motivoFila && (
          <Text size="xs" c="dimmed" mt={4}>
            Na fila por: {String(motivoFila.decisao ?? motivoFila.motivo ?? "regra")}
            {motivoFila.politica ? ` · ${String(motivoFila.politica)}` : ""}
          </Text>
        )}
      </div>
    </Stack>
  );
}
