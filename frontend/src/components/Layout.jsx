import { Outlet } from "react-router-dom";
import Navbar from "./Navbar";
import Footer from "./Footer";
import WhatsAppPopup from "./WhatsAppPopup";
import { useSeo } from "../hooks/useSeo";

// Main page wrapper used on every page.
// Renders: header (Navbar) → page content (Outlet) → footer (Footer) + popup (WhatsAppPopup)
// useSeo keeps the browser tab title and the Google canonical address
// in sync as the visitor moves between pages.
export default function Layout() {
  useSeo();

  return (
    <div className="min-h-screen flex flex-col">
      <Navbar />
      <main className="flex-1">
        <Outlet />
      </main>
      <Footer />
      <WhatsAppPopup />
    </div>
  );
}
