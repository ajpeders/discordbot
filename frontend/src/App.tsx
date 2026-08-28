import { BrowserRouter, Routes, Route } from "react-router-dom";
import { GuildProvider } from "./state/guild";
import Layout from "./components/Layout";
import DashboardPage from "./pages/DashboardPage";
import PlaylistsPage from "./pages/PlaylistsPage";
import PlaylistDetailPage from "./pages/PlaylistDetailPage";
import LocalPage from "./pages/LocalPage";
import HistoryPage from "./pages/HistoryPage";
import GamesPage from "./pages/GamesPage";
import LoginPage from "./pages/LoginPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <GuildProvider>
              <Layout />
            </GuildProvider>
          }
        >
          <Route path="/" element={<DashboardPage />} />
          <Route path="/playlists" element={<PlaylistsPage />} />
          <Route path="/playlists/:name" element={<PlaylistDetailPage />} />
          <Route path="/local" element={<LocalPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/games" element={<GamesPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
