import { useState, useEffect } from "react";
import { sobreAcademiaPage, siteConfig } from "../data";
import ImageGallery from "../components/ImageGallery";
import ContactSection from "../components/ContactSection";
import Reveal from "../components/Reveal";

const STREET_IN_TEXT = "C/ Hermanos Jiménez 25";

function highlightAddress(text) {
  const parts = text.split(STREET_IN_TEXT);
  if (parts.length === 1) return text;
  return parts.map((part, i) => (
    <span key={i}>
      {part}
      {i < parts.length - 1 && (
        <a
          href={siteConfig.mapsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary font-semibold hover:text-primary-dark transition-colors"
        >
          {STREET_IN_TEXT}
        </a>
      )}
    </span>
  ));
}

function TestimonialsCarousel({ testimonials }) {
  const [current, setCurrent] = useState(0);
  const n = testimonials.length;

  useEffect(() => {
    const timer = setInterval(() => setCurrent((c) => (c + 1) % n), 5000);
    return () => clearInterval(timer);
  }, [n]);

  const prev = () => setCurrent((c) => (c - 1 + n) % n);
  const next = () => setCurrent((c) => (c + 1) % n);

  return (
    <div className="max-w-2xl mx-auto text-center relative">
      {/* Decorative large background quote mark */}
      <div className="absolute -top-6 left-0 text-primary/8 pointer-events-none select-none" aria-hidden="true">
        <svg className="w-28 h-28" fill="currentColor" viewBox="0 0 24 24">
          <path d="M14.017 21v-7.391c0-5.704 3.731-9.57 8.983-10.609l.995 2.151c-2.432.917-3.995 3.638-3.995 5.849h4v10h-9.983zm-14.017 0v-7.391c0-5.704 3.748-9.57 9-10.609l.996 2.151c-2.433.917-3.996 3.638-3.996 5.849h3.983v10h-9.983z" />
        </svg>
      </div>

      {/* Arrows + grid wrapper — arrows are siblings of the grid so they don't affect its height */}
      <div className="relative px-10">
        <button
          onClick={prev}
          aria-label="Anterior"
          className="absolute left-0 top-1/2 -translate-y-1/2 text-primary/50 hover:text-primary transition-colors z-10 p-2 -m-2"
        >
          <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        {/* CSS grid stack — container auto-sizes to the tallest testimonial */}
        <div className="grid">
          {testimonials.map((t, i) => (
            <div
              key={i}
              className={`col-start-1 row-start-1 flex flex-col items-center transition-opacity duration-700 ${
                i === current ? "opacity-100" : "opacity-0 pointer-events-none"
              }`}
            >
              <p className="text-gray-700 italic leading-relaxed mb-3 text-justify">{t.text}</p>
              <p className="font-heading font-semibold text-primary-dark">— {t.author}</p>
            </div>
          ))}
        </div>

        <button
          onClick={next}
          aria-label="Siguiente"
          className="absolute right-0 top-1/2 -translate-y-1/2 text-primary/50 hover:text-primary transition-colors z-10 p-2 -m-2"
        >
          <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>

      {/* Dots */}
      <div className="flex justify-center gap-1 mt-6">
        {testimonials.map((_, i) => (
          <button
            key={i}
            onClick={() => setCurrent(i)}
            aria-label={`Testimonio ${i + 1}`}
            className="px-1.5 py-4 -my-3 flex items-center justify-center group/dot"
          >
            <span
              className={`w-2.5 h-2.5 rounded-full transition-all duration-300 block ${
                i === current ? "bg-primary scale-125" : "bg-gray-300 group-hover/dot:bg-primary/40"
              }`}
            />
          </button>
        ))}
      </div>
    </div>
  );
}

export default function SobreLaAcademia() {
  return (
    <>
      {/* 1. Split intro: image left + mint text right */}
      <section className="bg-white">
        <div className="flex flex-col md:flex-row min-h-105">
          <div className="md:w-1/2 overflow-hidden group">
            <img
              src={sobreAcademiaPage.image}
              alt="Interior de Five a Day English Academy, Albacete en C/ Hermanos Jiménez 25"
              loading="lazy"
              decoding="async"
              className="w-full h-64 md:h-full object-cover transition-transform duration-700 group-hover:scale-105"
            />
          </div>
          <div className="md:w-1/2 bg-mint-light flex flex-col justify-center p-8 md:p-12">
            <Reveal>
              <h1 className="font-title text-3xl md:text-4xl font-bold text-primary-dark mb-6">
                {sobreAcademiaPage.title}
              </h1>
              {sobreAcademiaPage.intro.split("\n\n").map((p, i) => (
                <p key={i} className="text-gray-700 leading-relaxed mb-4 text-justify">
                  {highlightAddress(p)}
                </p>
              ))}
            </Reveal>
          </div>
        </div>
      </section>

      {/* 2. Yellow section — differences list */}
      <section className="py-14 bg-accent-yellow">
        <div className="max-w-245 mx-auto px-4">
          <Reveal>
            <h2 className="text-3xl font-bold italic text-primary-darker text-center mb-4">
              {sobreAcademiaPage.differencesTitle}
            </h2>
            <div className="mt-2 mb-8 text-center">
              {sobreAcademiaPage.differencesIntro.split("\n\n").map((p, i) => (
                <p key={i} className="text-primary-darker/80 leading-relaxed mb-2">
                  {p}
                </p>
              ))}
            </div>
          </Reveal>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {sobreAcademiaPage.differences.map((diff, idx) => (
              <Reveal
                key={diff.title}
                delay={idx * 70}
                className="bg-white/70 hover:bg-white rounded-xl p-5 shadow-sm hover:-translate-y-1 hover:shadow-md transition-all duration-300"
              >
                <h3 className="font-heading font-bold text-primary-dark mb-2 flex items-start gap-2">
                  <span className="text-accent-green-dark shrink-0">✅</span>
                  {diff.title}
                </h3>
                <ul className="space-y-1 text-sm text-gray-700">
                  {diff.items.map((item, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="text-primary shrink-0 mt-0.5">•</span>
                      <span className="text-justify">{item}</span>
                    </li>
                  ))}
                </ul>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* 3. Photo gallery */}
      <section className="py-14 bg-white">
        <div className="max-w-245 mx-auto px-4">
          <Reveal>
            <h2 className="font-title text-3xl font-bold text-primary-dark text-center mb-10">
              {sobreAcademiaPage.gallerySectionTitle}
            </h2>
          </Reveal>
          <ImageGallery images={sobreAcademiaPage.gallery} />
        </div>
      </section>

      {/* 4. Testimonials */}
      <section className="py-14 bg-warm">
        <div className="max-w-245 mx-auto px-4">
          <Reveal>
            <h2 className="font-title text-3xl font-bold text-primary-dark text-center mb-10">
              {sobreAcademiaPage.testimonialsSectionTitle}
            </h2>
          </Reveal>
          <TestimonialsCarousel testimonials={sobreAcademiaPage.testimonials} />
        </div>
      </section>

      {/* 5. Google Maps */}
      <section className="py-14 bg-white">
        <div className="max-w-245 mx-auto px-4">
          <Reveal>
            <h2 className="font-title text-3xl font-bold text-primary-dark text-center mb-8">
              {sobreAcademiaPage.mapSectionTitle}
            </h2>
          </Reveal>
          <Reveal delay={100} className="rounded-xl overflow-hidden shadow-lg">
            <iframe
              title={sobreAcademiaPage.mapFrameTitle}
              src={siteConfig.mapsEmbedUrl}
              className="w-full h-72 md:h-96"
              style={{ border: 0 }}
              allowFullScreen=""
              loading="lazy"
              referrerPolicy="no-referrer-when-downgrade"
            />
          </Reveal>
          <div className="mt-4 text-center">
            <a
              href={siteConfig.mapsUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-primary hover:text-primary-dark font-heading font-semibold transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {siteConfig.mapsLinkLabel}
            </a>
          </div>
        </div>
      </section>

      <ContactSection />
    </>
  );
}
