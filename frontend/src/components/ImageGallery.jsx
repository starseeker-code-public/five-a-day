import { useState, useEffect, useCallback } from "react";

export default function ImageGallery({ images }) {
  const [current, setCurrent] = useState(0);
  const [lightbox, setLightbox] = useState(false);
  const n = images.length;

  const prev = useCallback(() => setCurrent(c => (c - 1 + n) % n), [n]);
  const next = useCallback(() => setCurrent(c => (c + 1) % n), [n]);

  useEffect(() => {
    if (!lightbox) return;
    const handler = (e) => {
      if (e.key === "Escape") setLightbox(false);
      if (e.key === "ArrowLeft") prev();
      if (e.key === "ArrowRight") next();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [lightbox, prev, next]);

  return (
    <div className="relative select-none">
      {/* Peek carousel */}
      <div className="overflow-hidden rounded-xl h-80 md:h-130 relative">
        {images.map((src, i) => {
          // Calculate position of this image relative to current.
          // Normalize offset to [-floor(n/2), ceil(n/2)] so wrapping takes shortest path.
          // For example, with 11 images: index 0 at offset -5, index 5 (center) at 0, index 10 at 5.
          const raw = ((i - current) % n + n) % n;
          const offset = raw > n / 2 ? raw - n : raw;
          return (
            <div
              key={i}
              className="absolute inset-y-0 w-[80%] px-3 transition-[left] duration-500 ease-in-out"
              style={{ left: `calc(10% + ${offset} * 80%)` }}
              onClick={() => {
                // Center image (offset 0) opens lightbox. Side images navigate left/right.
                if (offset === 0) setLightbox(true);
                else if (offset < 0) prev();
                else next();
              }}
            >
              <img
                src={src}
                alt={`Instalaciones y actividades de Five a Day English Academy, Albacete (foto ${i + 1})`}
                loading="lazy"
                decoding="async"
                className={`h-full w-full object-cover rounded-xl cursor-pointer transition-opacity duration-300 ${
                  offset === 0 ? "opacity-100" : "opacity-50 hover:opacity-70"
                }`}
              />
            </div>
          );
        })}
      </div>

      {/* Arrows */}
      <button
        onClick={prev}
        aria-label="Anterior"
        className="absolute left-[10%] top-1/2 -translate-y-1/2 z-10 bg-white/80 hover:bg-white text-primary-dark rounded-full p-3 shadow-md transition-colors"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
      </button>
      <button
        onClick={next}
        aria-label="Siguiente"
        className="absolute right-[10%] top-1/2 -translate-y-1/2 z-10 bg-white/80 hover:bg-white text-primary-dark rounded-full p-3 shadow-md transition-colors"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
      </button>

      {/* Dot indicators */}
      <div className="flex justify-center gap-0.5 mt-1">
        {images.map((_, i) => (
          <button
            key={i}
            onClick={() => setCurrent(i)}
            aria-label={`Imagen ${i + 1}`}
            className="px-1.5 py-4 -my-2 flex items-center justify-center group/dot"
          >
            <span
              className={`w-2.5 h-2.5 rounded-full transition-colors block ${
                i === current ? "bg-primary" : "bg-gray-300 group-hover/dot:bg-primary/50"
              }`}
            />
          </button>
        ))}
      </div>

      {/* Lightbox */}
      {lightbox && (
        <div
          className="fixed inset-0 bg-black/85 z-50 flex items-center justify-center p-4"
          onClick={() => setLightbox(false)}
        >
          <button
            aria-label="Cerrar"
            className="absolute top-4 right-4 text-white hover:text-gray-300 transition-colors p-2"
            onClick={() => setLightbox(false)}
          >
            <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
          <button
            aria-label="Anterior"
            className="absolute left-4 top-1/2 -translate-y-1/2 text-white bg-black/40 hover:bg-black/60 rounded-full p-3 transition-colors"
            onClick={(e) => { e.stopPropagation(); prev(); }}
          >
            <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <img
            src={images[current]}
            alt="Imagen ampliada"
            className="max-w-[85vw] max-h-[85vh] object-contain rounded-lg shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
          <button
            aria-label="Siguiente"
            className="absolute right-4 top-1/2 -translate-y-1/2 text-white bg-black/40 hover:bg-black/60 rounded-full p-3 transition-colors"
            onClick={(e) => { e.stopPropagation(); next(); }}
          >
            <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 text-white/70 text-sm">
            {current + 1} / {n}
          </div>
        </div>
      )}
    </div>
  );
}
