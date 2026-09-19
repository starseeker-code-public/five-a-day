import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import ScrollToTop from "./components/ScrollToTop";
import { usePreloadImages } from "./hooks/usePreloadImages";

// All image paths used in the site. Preloading these prevents flickering when users navigate.
const ALL_IMAGES = [
  "/images/logo_transparent.png",
  "/images/logo_contact.png",
  "/images/subvencion.png",
  "/images/home_1.jpg",
  "/images/home_2.jpg",
  "/images/home_3.jpg",
  "/images/home_4.jpg",
  "/images/home_5.jpg",
  "/images/penelope.png",
  "/images/silvia.png",
  "/images/metodologia_1.jpg",
  "/images/metodologia_2.jpg",
  "/images/metodologia_3.jpg",
  "/images/metodologia_4.jpg",
  "/images/metodologia_5.jpg",
  "/images/metodologia_6.jpg",
  "/images/ods.png",
  "/images/about.jpeg",
  "/images/about_galery_1.jpg",
  "/images/about_galery_2.jpg",
  "/images/about_galery_3.jpg",
  "/images/about_galery_4.jpg",
  "/images/about_galery_5.jpg",
  "/images/about_galery_6.jpg",
  "/images/about_galery_7.jpg",
  "/images/about_galery_8.jpg",
  "/images/about_galery_9.jpg",
  "/images/about_galery_10.jpg",
  "/images/about_galery_11.jpg",
  "/images/questions.jpeg",
];
import Home from "./pages/Home";
import QuienesSomos from "./pages/QuienesSomos";
import NuestraMetodologia from "./pages/NuestraMetodologia";
import LosODS from "./pages/LosODS";
import SobreLaAcademia from "./pages/SobreLaAcademia";
import PreguntasFrecuentes from "./pages/PreguntasFrecuentes";
import AvisoLegal from "./pages/AvisoLegal";

export default function App() {
  usePreloadImages(ALL_IMAGES);

  return (
    <BrowserRouter>
      <ScrollToTop />
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="quienes-somos" element={<QuienesSomos />} />
          <Route path="nuestra-metodologia" element={<NuestraMetodologia />} />
          <Route path="ods" element={<LosODS />} />
          <Route path="sobre-la-academia" element={<SobreLaAcademia />} />
          <Route path="faq" element={<PreguntasFrecuentes />} />
          <Route path="aviso-legal" element={<AvisoLegal />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
