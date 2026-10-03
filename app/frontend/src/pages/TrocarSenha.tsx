import { Alert, Button, Card, Center, PasswordInput, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { mensagemDeErro } from "../api/client";
import { auth } from "../api/servicos";
import { useAuth } from "../auth/AuthContext";

export function TrocarSenha() {
  const { usuario, recarregar, logout } = useAuth();
  const navegar = useNavigate();
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  const obrigatoria = usuario?.precisa_trocar_senha ?? false;

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    setErro(null);
    if (nova.length < 10) return setErro("a nova senha precisa ter ao menos 10 caracteres");
    if (nova !== confirmacao) return setErro("a confirmação não coincide");
    setEnviando(true);
    try {
      await auth.trocarSenha(atual, nova);
      // Trocar a senha revoga os refresh tokens: entra de novo com a senha nova.
      await logout();
      notifications.show({ color: "green", message: "Senha alterada. Entre novamente." });
      navegar("/login", { replace: true });
    } catch (err) {
      setErro(mensagemDeErro(err, "não foi possível trocar a senha"));
    } finally {
      setEnviando(false);
    }
  };

  return (
    <Center mih="80vh">
      <Card withBorder shadow="sm" w={420} p="xl">
        <form onSubmit={enviar}>
          <Stack>
            <div>
              <Title order={3}>Trocar senha</Title>
              {obrigatoria && (
                <Text c="orange" size="sm">
                  Sua senha inicial precisa ser trocada antes de continuar.
                </Text>
              )}
            </div>
            <PasswordInput
              label="Senha atual"
              value={atual}
              onChange={(e) => setAtual(e.currentTarget.value)}
              required
              autoFocus
            />
            <PasswordInput
              label="Nova senha"
              description="mínimo de 10 caracteres"
              value={nova}
              onChange={(e) => setNova(e.currentTarget.value)}
              required
            />
            <PasswordInput
              label="Confirmar nova senha"
              value={confirmacao}
              onChange={(e) => setConfirmacao(e.currentTarget.value)}
              required
            />
            {erro && <Alert color="red">{erro}</Alert>}
            <Button type="submit" loading={enviando}>
              Salvar nova senha
            </Button>
            {!obrigatoria && (
              <Button
                variant="subtle"
                onClick={() =>
                  recarregar()
                    .catch(() => undefined)
                    .then(() => navegar(-1))
                }
              >
                Cancelar
              </Button>
            )}
          </Stack>
        </form>
      </Card>
    </Center>
  );
}
