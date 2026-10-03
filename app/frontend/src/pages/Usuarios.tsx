import { Badge, Button, Group, Modal, PasswordInput, Select, Stack, Switch, Table, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { mensagemDeErro } from "../api/client";
import { usuarios, type Papel, type UsuarioPatch } from "../api/servicos";
import { useAuth } from "../auth/AuthContext";

const PAPEIS: Papel[] = ["auditor", "supervisor", "admin"];

/** Admin: criar, desativar e trocar papel. Sem exclusao fisica. */
export function Usuarios() {
  const qc = useQueryClient();
  const { usuario: eu } = useAuth();
  const lista = useQuery({ queryKey: ["usuarios"], queryFn: usuarios.listar });
  const [aberto, setAberto] = useState(false);
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [papel, setPapel] = useState<Papel>("auditor");

  const invalidar = () => void qc.invalidateQueries({ queryKey: ["usuarios"] });
  const aoErro = (e: unknown) => notifications.show({ color: "red", message: mensagemDeErro(e) });

  const criar = useMutation({
    mutationFn: () => usuarios.criar({ nome, email, senha_inicial: senha, papel }),
    onSuccess: () => {
      notifications.show({ color: "green", message: "Usuário criado. Ele troca a senha no primeiro acesso." });
      setAberto(false);
      setNome("");
      setEmail("");
      setSenha("");
      invalidar();
    },
    onError: aoErro,
  });
  const atualizar = useMutation({
    mutationFn: ({ id, dados }: { id: number; dados: UsuarioPatch }) => usuarios.atualizar(id, dados),
    onSuccess: invalidar,
    onError: aoErro,
  });

  const enviar = (e: FormEvent) => {
    e.preventDefault();
    criar.mutate();
  };

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Usuários</Title>
        <Button onClick={() => setAberto(true)}>Novo usuário</Button>
      </Group>
      <Table striped withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Nome</Table.Th>
            <Table.Th>E-mail</Table.Th>
            <Table.Th>Papel</Table.Th>
            <Table.Th>Senha</Table.Th>
            <Table.Th>Ativo</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {lista.data?.map((u) => (
            <Table.Tr key={u.id}>
              <Table.Td>{u.nome}</Table.Td>
              <Table.Td>{u.email}</Table.Td>
              <Table.Td>
                <Select
                  data={PAPEIS}
                  value={u.papel}
                  disabled={u.id === eu?.id}
                  onChange={(v) => v && atualizar.mutate({ id: u.id, dados: { papel: v as Papel } })}
                  w={140}
                  size="xs"
                />
              </Table.Td>
              <Table.Td>
                {u.precisa_trocar_senha ? <Badge color="orange">troca pendente</Badge> : <Badge color="gray" variant="light">ok</Badge>}
              </Table.Td>
              <Table.Td>
                <Switch
                  checked={u.ativo}
                  disabled={u.id === eu?.id}
                  onChange={(e) => atualizar.mutate({ id: u.id, dados: { ativo: e.currentTarget.checked } })}
                />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Modal opened={aberto} onClose={() => setAberto(false)} title="Novo usuário">
        <form onSubmit={enviar}>
          <Stack>
            <TextInput label="Nome" value={nome} onChange={(e) => setNome(e.currentTarget.value)} required />
            <TextInput label="E-mail" type="email" value={email} onChange={(e) => setEmail(e.currentTarget.value)} required />
            <PasswordInput
              label="Senha inicial"
              description="mínimo de 10 caracteres; o usuário troca no primeiro acesso"
              value={senha}
              onChange={(e) => setSenha(e.currentTarget.value)}
              required
              minLength={10}
            />
            <Select label="Papel" data={PAPEIS} value={papel} onChange={(v) => v && setPapel(v as Papel)} />
            <Button type="submit" loading={criar.isPending}>
              Criar
            </Button>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
