import { Button, Group, Kbd, Radio, Stack, Text, Textarea } from "@mantine/core";

import { MOTIVOS, type MotivoReprovacao } from "../api/servicos";

interface Props {
  motivo: MotivoReprovacao | null;
  observacao: string;
  ocupado: boolean;
  onMotivo: (m: MotivoReprovacao | null) => void;
  onObservacao: (texto: string) => void;
  onAprovar: () => void;
  onReprovar: () => void;
  onPular: () => void;
}

/** Aprovar, reprovar (motivo em lista fechada) e pular, com os atalhos visiveis. */
export function BotoesDecisao(p: Props) {
  const reprovarHabilitado = p.motivo !== null && (p.motivo !== "outro" || p.observacao.trim() !== "");
  return (
    <Stack gap="sm">
      <Radio.Group
        label="Motivo da reprovação"
        description="obrigatório para reprovar · teclas 1 a 6"
        value={p.motivo ?? ""}
        onChange={(v) => p.onMotivo((v || null) as MotivoReprovacao | null)}
      >
        <Stack gap={4} mt={6}>
          {MOTIVOS.map((m) => (
            <Radio
              key={m.valor}
              value={m.valor}
              label={
                <Group gap={6}>
                  <Kbd size="xs">{m.tecla}</Kbd>
                  <span>{m.rotulo}</span>
                </Group>
              }
            />
          ))}
        </Stack>
      </Radio.Group>
      <Textarea
        label="Observação"
        placeholder={p.motivo === "outro" ? "obrigatória para 'outro'" : "opcional"}
        value={p.observacao}
        onChange={(e) => p.onObservacao(e.currentTarget.value)}
        autosize
        minRows={2}
      />
      <Group grow>
        <Button
          color="green"
          size="md"
          onClick={p.onAprovar}
          loading={p.ocupado}
          data-testid="btn-aprovar"
        >
          Aprovar <Kbd ml={8}>A</Kbd>
        </Button>
        <Button
          color="red"
          size="md"
          onClick={p.onReprovar}
          disabled={!reprovarHabilitado}
          loading={p.ocupado}
          data-testid="btn-reprovar"
        >
          Reprovar <Kbd ml={8}>R</Kbd>
        </Button>
      </Group>
      <Button variant="default" onClick={p.onPular} disabled={p.ocupado} data-testid="btn-pular">
        Pular <Kbd ml={8}>Espaço</Kbd>
      </Button>
      <Text size="xs" c="dimmed">
        Aprovar confirma que a foto é legível e coerente com o declarado. Reprovar devolve a
        leitura para revisita em campo.
      </Text>
    </Stack>
  );
}
