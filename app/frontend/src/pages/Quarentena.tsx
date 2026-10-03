import {
  Badge,
  Button,
  Group,
  Modal,
  Pagination,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { mensagemDeErro } from "../api/client";
import { quarentena, type QuarentenaOut } from "../api/servicos";

const TIPOS = [
  "nota_exige_sem_foto",
  "foto_referenciada_ausente",
  "foto_sem_exigencia",
  "codigo_nao_mapeado",
  "foto_orfa",
  "medidor_multiplo",
  "posicao_invalida",
  "posicao_ausente",
];
const POR_PAGINA = 50;

type Aba = "bloqueantes" | "informativas" | "resolvidas";

/** Supervisao: inconsistencias de dados. Bloqueantes primeiro, como pede o plano. */
export function Quarentena() {
  const qc = useQueryClient();
  const [aba, setAba] = useState<Aba>("bloqueantes");
  const [tipo, setTipo] = useState<string | null>(null);
  const [pagina, setPagina] = useState(1);
  const [resolvendo, setResolvendo] = useState<QuarentenaOut | null>(null);
  const [observacao, setObservacao] = useState("");

  const params = {
    tipo: tipo ?? undefined,
    severidade: aba === "resolvidas" ? undefined : aba === "bloqueantes" ? "bloqueante" : "informativo",
    resolvido: aba === "resolvidas",
    pagina,
    por_pagina: POR_PAGINA,
  };
  const lista = useQuery({ queryKey: ["quarentena", params], queryFn: () => quarentena.listar(params) });
  const resumo = useQuery({ queryKey: ["quarentena", "resumo"], queryFn: quarentena.resumo });

  const resolver = useMutation({
    mutationFn: ({ id, obs }: { id: number; obs: string }) => quarentena.resolver(id, obs || undefined),
    onSuccess: () => {
      notifications.show({ color: "green", message: "Registro resolvido." });
      setResolvendo(null);
      setObservacao("");
      void qc.invalidateQueries({ queryKey: ["quarentena"] });
    },
    onError: (e) => notifications.show({ color: "red", message: mensagemDeErro(e) }),
  });

  const totalPaginas = Math.max(1, Math.ceil((lista.data?.total ?? 0) / POR_PAGINA));

  return (
    <Stack>
      <Group justify="space-between" align="end">
        <Title order={3}>Quarentena</Title>
        <Group gap="xs">
          {resumo.data &&
            Object.entries(resumo.data).map(([t, n]) => (
              <Badge key={t} variant="light" color={n.pendentes ? "yellow" : "gray"}>
                {t}: {n.pendentes}
              </Badge>
            ))}
        </Group>
      </Group>
      <Group>
        <Tabs
          value={aba}
          onChange={(v) => {
            setAba((v as Aba) ?? "bloqueantes");
            setPagina(1);
          }}
        >
          <Tabs.List>
            <Tabs.Tab value="bloqueantes">Bloqueantes</Tabs.Tab>
            <Tabs.Tab value="informativas">Informativas</Tabs.Tab>
            <Tabs.Tab value="resolvidas">Resolvidas</Tabs.Tab>
          </Tabs.List>
        </Tabs>
        <Select
          placeholder="todos os tipos"
          clearable
          data={TIPOS}
          value={tipo}
          onChange={(v) => {
            setTipo(v);
            setPagina(1);
          }}
          w={260}
        />
        <Text size="sm" c="dimmed">
          {lista.data?.total ?? "…"} registros
        </Text>
      </Group>

      <Table striped highlightOnHover withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>#</Table.Th>
            <Table.Th>Tipo</Table.Th>
            <Table.Th>Severidade</Table.Th>
            <Table.Th>Leitura</Table.Th>
            <Table.Th>Detalhe</Table.Th>
            <Table.Th>Criado</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {lista.data?.itens.map((q) => (
            <Table.Tr key={q.id}>
              <Table.Td>{q.id}</Table.Td>
              <Table.Td>
                <Badge variant="light">{q.tipo}</Badge>
              </Table.Td>
              <Table.Td>
                <Badge color={q.severidade === "bloqueante" ? "red" : "yellow"}>{q.severidade}</Badge>
              </Table.Td>
              <Table.Td>{q.leitura_id ?? (q.foto_id ? `foto ${q.foto_id}` : "—")}</Table.Td>
              <Table.Td>
                <Text size="xs" ff="monospace" lineClamp={2}>
                  {q.detalhe ? JSON.stringify(q.detalhe) : ""}
                </Text>
              </Table.Td>
              <Table.Td>{new Date(q.criado_em).toLocaleDateString("pt-BR")}</Table.Td>
              <Table.Td>
                {q.resolvido ? (
                  <Text size="xs" c="dimmed">
                    {q.resolvido_por}
                  </Text>
                ) : (
                  <Button size="xs" variant="light" onClick={() => setResolvendo(q)}>
                    Resolver
                  </Button>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Pagination total={totalPaginas} value={pagina} onChange={setPagina} />

      <Modal opened={resolvendo !== null} onClose={() => setResolvendo(null)} title="Resolver registro">
        <Stack>
          <Text size="sm">
            <b>{resolvendo?.tipo}</b> · leitura {resolvendo?.leitura_id ?? "—"}
          </Text>
          <Textarea
            label="Observação"
            value={observacao}
            onChange={(e) => setObservacao(e.currentTarget.value)}
            autosize
            minRows={3}
          />
          <Button
            loading={resolver.isPending}
            onClick={() => resolvendo && resolver.mutate({ id: resolvendo.id, obs: observacao })}
          >
            Marcar como resolvido
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
