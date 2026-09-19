import { Alert, Button, Card, Center, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { mensagemDeErro } from "../api/client";
import { useAuth } from "../auth/AuthContext";

export function Login() {
  const { usuario, carregando, login } = useAuth();
  const navegar = useNavigate();
  const local = useLocation() as { state?: { de?: string } };
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  if (!carregando && usuario) {
    return <Navigate to={usuario.precisa_trocar_senha ? "/trocar-senha" : "/fila"} replace />;
  }

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    setErro(null);
    setEnviando(true);
    try {
      const me = await login(email, senha);
      navegar(me.precisa_trocar_senha ? "/trocar-senha" : (local.state?.de ?? "/fila"), {
        replace: true,
      });
    } catch (err) {
      setErro(mensagemDeErro(err, "não foi possível entrar"));
    } finally {
      setEnviando(false);
    }
  };

  return (
    <Center mih="100vh" bg="gray.0">
      <Card withBorder shadow="sm" w={380} p="xl">
        <form onSubmit={enviar}>
          <Stack>
            <div>
              <Title order={3}>Inspeção de Fotos de Leitura</Title>
              <Text c="dimmed" size="sm">
                Neoenergia Pernambuco · painel de auditoria
              </Text>
            </div>
            <TextInput
              label="E-mail"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
              required
              autoFocus
            />
            <PasswordInput
              label="Senha"
              autoComplete="current-password"
              value={senha}
              onChange={(e) => setSenha(e.currentTarget.value)}
              required
            />
            {erro && (
              <Alert color="red" data-testid="erro-login">
                {erro}
              </Alert>
            )}
            <Button type="submit" loading={enviando} fullWidth>
              Entrar
            </Button>
          </Stack>
        </form>
      </Card>
    </Center>
  );
}
