import { Outlet } from "react-router-dom";
import Navbar from "./Navbar";
import Footer from "./Footer";
import WhatsAppPopup from "./WhatsAppPopup";

// Main page wrapper used on every page.
// Renders: header (Navbar) → page content (Outlet) → footer (Footer) + popup (WhatsAppPopup)
export default function Layout() {
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
