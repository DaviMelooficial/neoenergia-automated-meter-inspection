import {
  Alert,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Kbd,
  SegmentedControl,
  Stack,
  Tabs,
  Text,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import axios from "axios";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { mensagemDeErro } from "../api/client";
import { operacao, rotulagem, type FotoRotulagemOut, type RodadaTela } from "../api/servicos";
import {
  alternarDefeito,
  CENAS,
  completo,
  DEFEITOS,
  escolherCena,
  escolherLegivel,
  LEGIVEL,
  legivelImposto,
  RASCUNHO_VAZIO,
  type RascunhoRotulo,
} from "../components/esquemaRotulagem";
import { PainelKappa } from "../components/PainelKappa";
import { useAtalhos } from "../components/useAtalhos";
import { VisualizadorFoto } from "../components/VisualizadorFoto";

const RODADAS: { value: RodadaTela; label: string }[] = [
  { value: "a", label: "Rodada A" },
  { value: "b", label: "Rodada B" },
  { value: "desempate", label: "Desempate" },
];

function Opcao({
  ativo,
  desabilitado = false,
  tecla,
  rotulo,
  definicao,
  onClick,
}: {
  ativo: boolean;
  desabilitado?: boolean;
  tecla: string;
  rotulo: string;
  definicao: string;
  onClick: () => void;
}) {
  return (
    <UnstyledButton
      onClick={onClick}
      disabled={desabilitado}
      aria-pressed={ativo}
      p={8}
      style={{
        borderRadius: 6,
        border: `1px solid ${ativo ? "#1C7C54" : "#DEE2E6"}`,
        background: ativo ? "#E4F4EB" : "transparent",
        opacity: desabilitado ? 0.45 : 1,
      }}
    >
      <Group gap={6} wrap="nowrap" align="flex-start">
        <Kbd size="xs">{tecla.toUpperCase()}</Kbd>
        <div>
          <Text size="sm" fw={600}>
            {rotulo}
          </Text>
          <Text size="xs" c="dimmed">
            {definicao}
          </Text>
        </div>
      </Group>
    </UnstyledButton>
  );
}

/**
 * Rotulagem dupla-cega do M6, esquema v2. O servidor so devolve a foto: nada de
 * leitura, nota, score ou rotulo de outra pessoa. Atalhos do app do Lab 2:
 * 1-4 cena, Q W E R T defeitos, S/N legivel, Enter grava. Zoom e pan como na fila.
 */
function Rotular() {
  const [rodada, setRodada] = useState<RodadaTela>("a");
  const [atual, setAtual] = useState<FotoRotulagemOut | null>(null);
  const [vazia, setVazia] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [rascunho, setRascunho] = useState<RascunhoRotulo>(RASCUNHO_VAZIO);
  const [src, setSrc] = useState<string | null>(null);
  const [gravadas, setGravadas] = useState(0);
  const inicio = useRef(Date.now());

  const carregar = useCallback(async (r: RodadaTela) => {
    setErro(null);
    setRascunho(RASCUNHO_VAZIO);
    try {
      const proxima = await rotulagem.proxima(r);
      setAtual(proxima);
      setVazia(false);
      inicio.current = Date.now();
    } catch (err) {
      setAtual(null);
      if (axios.isAxiosError(err) && err.response?.status === 404) setVazia(true);
      else setErro(mensagemDeErro(err, "não foi possível carregar a próxima foto"));
    }
  }, []);

  useEffect(() => {
    void carregar(rodada);
  }, [rodada, carregar]);

  // A rota da foto exige token: baixa o binario e revoga o object URL ao trocar.
  const url = atual?.url ?? null;
  useEffect(() => {
    if (!url) {
      setSrc(null);
      return;
    }
    let criado: string | null = null;
    let cancelado = false;
    operacao
      .fotoBlob(url)
      .then((u) => {
        criado = u;
        if (!cancelado) setSrc(u);
      })
      .catch(() => setErro("não foi possível baixar a foto"));
    return () => {
      cancelado = true;
      if (criado) URL.revokeObjectURL(criado);
    };
  }, [url]);

  const gravar = useCallback(async () => {
    if (!atual || ocupado) return;
    if (!completo(rascunho)) {
      notifications.show({ color: "orange", message: "Escolha a cena (1-4) e se é legível (S/N)." });
      return;
    }
    setOcupado(true);
    try {
      await rotulagem.gravar(atual.foto_id, {
        rodada,
        cena: rascunho.cena!,
        defeitos: rascunho.defeitos,
        legivel: rascunho.legivel!,
        segundos_na_foto: Math.round((Date.now() - inicio.current) / 100) / 10,
      });
      setGravadas((n) => n + 1);
      await carregar(rodada);
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        notifications.show({ color: "yellow", message: "Esta foto não está mais disponível para você." });
        await carregar(rodada);
      } else {
        setErro(mensagemDeErro(err, "não foi possível gravar o rótulo"));
      }
    } finally {
      setOcupado(false);
    }
  }, [atual, ocupado, rascunho, rodada, carregar]);

  const atalhos = useMemo(() => {
    const mapa: Record<string, () => void> = {
      enter: () => void gravar(),
      [LEGIVEL.sim.tecla]: () => setRascunho((r) => escolherLegivel(r, true)),
      [LEGIVEL.nao.tecla]: () => setRascunho((r) => escolherLegivel(r, false)),
    };
    for (const c of CENAS) mapa[c.tecla] = () => setRascunho((r) => escolherCena(r, c.valor));
    for (const d of DEFEITOS) mapa[d.tecla] = () => setRascunho((r) => alternarDefeito(r, d.valor));
    return mapa;
  }, [gravar]);
  useAtalhos(atalhos, atual !== null);

  const imposto = legivelImposto(rascunho);

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Group gap="xs">
          <SegmentedControl
            value={rodada}
            onChange={(v) => {
              setRodada(v as RodadaTela);
              // O SegmentedControl e um radio: com o foco nele os atalhos ficariam mudos.
              (document.activeElement as HTMLElement | null)?.blur();
            }}
            data={RODADAS}
            aria-label="rodada"
          />
          {atual && (
            <Badge variant="light" ff="monospace" data-testid="foto-atual">
              foto #{atual.foto_id}
            </Badge>
          )}
        </Group>
        <Text size="sm" c="dimmed" role="status" aria-live="polite">
          {atual && (
            <>
              restantes: <b>{atual.restantes}</b> ·{" "}
            </>
          )}
          gravadas nesta sessão: <b>{gravadas}</b>
        </Text>
      </Group>
      {erro && (
        <Alert color="red" withCloseButton onClose={() => setErro(null)} role="alert">
          {erro}
        </Alert>
      )}
      {vazia ? (
        <Alert color="green" title="Nada a rotular" role="status">
          Não há foto disponível para você nesta rodada.
          <Group mt="sm">
            <Button variant="light" onClick={() => void carregar(rodada)}>
              Verificar de novo
            </Button>
          </Group>
        </Alert>
      ) : (
        <Grid gutter="md" align="stretch">
          <Grid.Col span={{ base: 12, md: 6 }}>
            <VisualizadorFoto
              src={src}
              carregando={atual === null || (url !== null && src === null)}
              alt={atual ? `foto ${atual.foto_id} para rotular` : ""}
              ativo={atual !== null}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Stack gap="sm">
              <Card withBorder>
                <Text fw={700} mb={6}>
                  1. Cena (uma só)
                </Text>
                <Stack gap={6}>
                  {CENAS.map((c) => (
                    <Opcao
                      key={c.valor}
                      ativo={rascunho.cena === c.valor}
                      tecla={c.tecla}
                      rotulo={c.rotulo}
                      definicao={c.definicao}
                      onClick={() => setRascunho((r) => escolherCena(r, c.valor))}
                    />
                  ))}
                </Stack>
              </Card>
              <Card withBorder>
                <Text fw={700} mb={6}>
                  2. Defeitos (zero, um ou vários)
                </Text>
                <Stack gap={6}>
                  {DEFEITOS.map((d) => (
                    <Opcao
                      key={d.valor}
                      ativo={rascunho.defeitos.includes(d.valor)}
                      desabilitado={rascunho.cena === "nao_medidor"}
                      tecla={d.tecla}
                      rotulo={d.rotulo}
                      definicao={d.definicao}
                      onClick={() => setRascunho((r) => alternarDefeito(r, d.valor))}
                    />
                  ))}
                </Stack>
              </Card>
              <Card withBorder>
                <Text fw={700} mb={6}>
                  3. Leitura legível?
                </Text>
                <Group grow align="stretch">
                  <Opcao
                    ativo={rascunho.legivel === true}
                    desabilitado={imposto !== null}
                    tecla={LEGIVEL.sim.tecla}
                    rotulo="Sim"
                    definicao={LEGIVEL.sim.definicao}
                    onClick={() => setRascunho((r) => escolherLegivel(r, true))}
                  />
                  <Opcao
                    ativo={rascunho.legivel === false}
                    desabilitado={imposto !== null}
                    tecla={LEGIVEL.nao.tecla}
                    rotulo="Não"
                    definicao={LEGIVEL.nao.definicao}
                    onClick={() => setRascunho((r) => escolherLegivel(r, false))}
                  />
                </Group>
                {imposto !== null && (
                  <Text size="xs" c="dimmed" mt={6}>
                    Legível = não, imposto pela cena ou por display apagado.
                  </Text>
                )}
              </Card>
              <Button
                onClick={() => void gravar()}
                disabled={!atual || !completo(rascunho)}
                loading={ocupado}
                rightSection={<Kbd size="xs">Enter</Kbd>}
              >
                Gravar e próxima
              </Button>
            </Stack>
          </Grid.Col>
        </Grid>
      )}
    </Stack>
  );
}

export function Rotulagem() {
  const [aba, setAba] = useState<string | null>("rotular");
  return (
    <Stack gap="sm">
      <Title order={3}>Rotulagem</Title>
      <Tabs value={aba} onChange={setAba} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="rotular">Rotular</Tabs.Tab>
          <Tabs.Tab value="kappa">Kappa por classe</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="rotular" pt="sm">
          <Rotular />
        </Tabs.Panel>
        <Tabs.Panel value="kappa" pt="sm">
          <PainelKappa />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
