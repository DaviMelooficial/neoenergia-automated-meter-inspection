import { Navigate, Route, Routes } from "react-router-dom";

import { RotaProtegida } from "./auth/RotaProtegida";
import { Fila } from "./pages/Fila";
import { Layout } from "./pages/Layout";
import { Login } from "./pages/Login";
import { Metricas } from "./pages/Metricas";
import { Quarentena } from "./pages/Quarentena";
import { TrocarSenha } from "./pages/TrocarSenha";
import { Usuarios } from "./pages/Usuarios";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />

      <Route element={<RotaProtegida />}>
        <Route path="/trocar-senha" element={<TrocarSenha />} />
        <Route element={<Layout />}>
          <Route path="/fila" element={<Fila />} />
          <Route element={<RotaProtegida minimo="supervisor" />}>
            <Route path="/quarentena" element={<Quarentena />} />
            <Route path="/metricas" element={<Metricas />} />
          </Route>
          <Route element={<RotaProtegida minimo="admin" />}>
            <Route path="/usuarios" element={<Usuarios />} />
          </Route>
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/fila" replace />} />
    </Routes>
  );
}
