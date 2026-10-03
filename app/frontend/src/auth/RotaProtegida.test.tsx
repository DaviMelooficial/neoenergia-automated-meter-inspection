import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ORDEM_PAPEL, type Papel, type UsuarioOut } from "../api/servicos";
import { AuthContext, type EstadoAuth } from "./AuthContext";
import { RotaProtegida } from "./RotaProtegida";

function usuario(papel: Papel, precisaTrocar = false): UsuarioOut {
  return {
    id: 1,
    nome: "Teste",
    email: "t@teste.com.br",
    papel,
    ativo: true,
    precisa_trocar_senha: precisaTrocar,
    criado_em: new Date().toISOString(),
  };
}

function renderizar(estado: Partial<EstadoAuth>, rotaInicial: string, minimo: Papel = "auditor") {
  const valor: EstadoAuth = {
    usuario: null,
    carregando: false,
    login: vi.fn(),
    logout: vi.fn(),
    recarregar: vi.fn(),
    temPapel: (m) => estado.usuario != null && ORDEM_PAPEL[estado.usuario.papel] >= ORDEM_PAPEL[m],
    ...estado,
  };
  return render(
    <MantineProvider>
      <AuthContext.Provider value={valor}>
        <MemoryRouter initialEntries={[rotaInicial]}>
          <Routes>
            <Route path="/login" element={<div>tela de login</div>} />
            <Route path="/trocar-senha" element={<div>tela de troca de senha</div>} />
            <Route element={<RotaProtegida minimo={minimo} />}>
              <Route path="/protegida" element={<div>conteudo protegido</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </MantineProvider>,
  );
}

describe("RotaProtegida", () => {
  it("redireciona para /login sem sessao", () => {
    renderizar({ usuario: null }, "/protegida");
    expect(screen.getByText("tela de login")).toBeInTheDocument();
  });

  it("mostra loader enquanto a sessao carrega", () => {
    renderizar({ usuario: null, carregando: true }, "/protegida");
    expect(screen.queryByText("tela de login")).not.toBeInTheDocument();
    expect(screen.queryByText("conteudo protegido")).not.toBeInTheDocument();
  });

  it("forca a troca da senha inicial", () => {
    renderizar({ usuario: usuario("admin", true) }, "/protegida");
    expect(screen.getByText("tela de troca de senha")).toBeInTheDocument();
  });

  it("libera quem tem o papel minimo", () => {
    renderizar({ usuario: usuario("auditor") }, "/protegida", "auditor");
    expect(screen.getByText("conteudo protegido")).toBeInTheDocument();
  });

  it("papel superior tambem passa (hierarquia)", () => {
    renderizar({ usuario: usuario("admin") }, "/protegida", "supervisor");
    expect(screen.getByText("conteudo protegido")).toBeInTheDocument();
  });

  it("bloqueia papel insuficiente com aviso, sem redirecionar", () => {
    renderizar({ usuario: usuario("auditor") }, "/protegida", "supervisor");
    expect(screen.getByTestId("sem-permissao")).toBeInTheDocument();
    expect(screen.queryByText("conteudo protegido")).not.toBeInTheDocument();
  });
});
