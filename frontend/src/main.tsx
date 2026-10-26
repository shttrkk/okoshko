import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import "@fontsource-variable/manrope";
import { AppProvider } from "./state";
import { Layout } from "./components/Layout";
import { Home, SearchPage } from "./pages/Search";
import { HotelPage } from "./pages/Hotel";
import { ComparePage } from "./pages/Compare";
import { BookingPage } from "./pages/Booking";
import {
  AgentPage,
  HotelierPage,
  SourcesPage,
  StudioPage,
} from "./pages/Operations";
import { ChangesPage, LabelPage, QualityPage } from "./pages/Quality";
import { SnapshotPage } from "./pages/Snapshot";
import "./styles.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route path="snapshot/:id" element={<SnapshotPage />} />
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="hotel/:id" element={<HotelPage />} />
            <Route path="compare" element={<ComparePage />} />
            <Route path="booking/:id" element={<BookingPage />} />
            <Route path="hotelier" element={<HotelierPage />} />
            <Route path="studio" element={<StudioPage />} />
            <Route path="agent" element={<AgentPage />} />
            <Route path="changes" element={<ChangesPage />} />
            <Route path="quality" element={<QualityPage />} />
            <Route path="quality/label" element={<LabelPage />} />
            <Route path="sources" element={<SourcesPage />} />
            <Route
              path="*"
              element={
                <div className="page-container empty">
                  <h1>Это окно пока закрыто.</h1>
                  <p>Похоже, такой страницы нет.</p>
                  <Link className="button primary" to="/">
                    На главную
                  </Link>
                </div>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProvider>
  </React.StrictMode>,
);
