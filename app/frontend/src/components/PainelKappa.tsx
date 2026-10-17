import { Alert, Badge, Group, Loader, SegmentedControl, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { mensagemDeErro } from "../api/client";
import { rotulagem, type ConsolidacaoOut, type RodadaRotulagem } from "../api/servicos";

const CONJUNTOS: { value: string; label: string; rodadas: RodadaRotulagem[] }[] = [
  { value: "lab02", label: "Lab 2 (importado)", rodadas: ["lab02"] },
  { value: "ab", label: "Rodadas A + B", rodadas: ["a", "b"] },
  { value: "abd", label: "A + B + desempate", rodadas: ["a", "b", "desempate"] },
];

const fmt = (k: number | null | undefined) =>
  k === null || k === undefined ? "n/d" : k.toFixed(2).replace(".", ",");

function Gate({ kappa, passa, limiar }: { kappa: number | null; passa: boolean; limiar: number }) {
  if (kappa === null) return <Badge color="gray" variant="light">sem dado</Badge>;
  const alvo = fmt(limiar);
  return passa ? (
    <Badge color="green" variant="light">passa (≥ {alvo})</Badge>
  ) : (
    <Badge color="red" variant="filled">não passa (&lt; {alvo})</Badge>
  );
}

/**
 * Kappa de Cohen por classe, media dos pares de rotuladores ponderada pelo n de
 * fotos em comum, com o gate do M6 sinalizado. So agregados: nenhum rotulo por foto,
 * entao pode ficar visivel para quem ainda esta rotulando.
 */
export function PainelKappa() {
  const [conjunto, setConjunto] = useState("lab02");
  const rodadas = CONJUNTOS.find((c) => c.value === conjunto)!.rodadas;
  const { data, isLoading, error } = useQuery<ConsolidacaoOut>({
    queryKey: ["rotulagem", "consolidacao", conjunto],
    queryFn: () => rotulagem.consolidacao(rodadas),
  });

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={4}>Concordância entre rotuladores</Title>
        <SegmentedControl
          size="xs"
          value={conjunto}
          onChange={setConjunto}
          data={CONJUNTOS.map(({ value, label }) => ({ value, label }))}
        />
      </Group>
      {isLoading && <Loader size="sm" />}
      {error && (
        <Alert color="red" role="alert">
          {mensagemDeErro(error, "não foi possível calcular o kappa")}
        </Alert>
      )}
      {data && (
        <>
          <Text size="sm" c="dimmed">
            {data.rotulos} rótulos, {data.fotos} fotos, {data.fotos_dupla_rotulagem} com dupla
            rotulagem, {data.fotos_com_empate} com empate na maioria. Gate do M6: κ ≥{" "}
            {fmt(data.limiar)}.
          </Text>
          <Table striped withTableBorder aria-label="kappa por classe">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>classe</Table.Th>
                <Table.Th>κ</Table.Th>
                <Table.Th>pares-foto</Table.Th>
                <Table.Th>gate</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.por_classe.map((c) => (
                <Table.Tr key={c.classe} data-testid={`kappa-${c.classe}`}>
                  <Table.Td>{c.classe}</Table.Td>
                  <Table.Td ff="monospace">{fmt(c.kappa)}</Table.Td>
                  <Table.Td>{c.n}</Table.Td>
                  <Table.Td>
                    <Gate kappa={c.kappa} passa={c.passa} limiar={data.limiar} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {data.pares.length > 0 ? (
            <Table withTableBorder aria-label="kappa por par">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>par</Table.Th>
                  <Table.Th>fotos</Table.Th>
                  {data.por_classe.map((c) => (
                    <Table.Th key={c.classe}>{c.classe}</Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.pares.map((p) => (
                  <Table.Tr key={`${p.a}-${p.b}`}>
                    <Table.Td>
                      {p.a} × {p.b}
                    </Table.Td>
                    <Table.Td>{p.n}</Table.Td>
                    {data.por_classe.map((c) => (
                      <Table.Td key={c.classe} ff="monospace">
                        {fmt(p.kappa[c.classe])}
                      </Table.Td>
                    ))}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          ) : (
            <Text size="sm" c="dimmed">
              Nenhum par de rotuladores com foto em comum neste conjunto.
            </Text>
          )}
        </>
      )}
    </Stack>
  );
}
