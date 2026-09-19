import { odsPage } from "../data";
import ContactSection from "../components/ContactSection";
import Reveal from "../components/Reveal";

export default function LosODS() {
  return (
    <>
      {/* Intro */}
      <section className="py-12 bg-white">
        <div className="max-w-245 mx-auto px-4">
          <Reveal>
            <h1 className="font-title text-4xl font-bold text-primary-dark mb-6 text-center">
              Five a Day y los ODS
            </h1>
            {odsPage.intro.split("\n\n").map((p, i) => (
              <p key={i} className="text-primary-dark text-lg leading-relaxed mb-5 text-center">
                {p}
              </p>
            ))}
          </Reveal>
        </div>
      </section>

      {/* SDG Image */}
      <section className="py-8 bg-warm">
        <div className="max-w-245 mx-auto px-4 text-center">
          <Reveal>
            <img
              src={odsPage.sdgImage}
              alt="Objetivos de Desarrollo Sostenible"
              className="mx-auto max-w-full rounded-lg shadow-md mb-4 transition-transform duration-500 hover:scale-[1.01]"
            />
            <p className="text-sm text-primary-dark/60 text-center">
              {odsPage.sdgGuidelinesNote}{" "}
              <a
                href={odsPage.sdgManualUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="underline text-primary hover:text-primary-dark"
              >
                Descargar manual
              </a>
            </p>
          </Reveal>
        </div>
      </section>

      {/* Education for Sustainable Development */}
      <section className="py-12 bg-white">
        <div className="max-w-245 mx-auto px-4">
          <Reveal>
            <h2 className="font-title text-3xl font-bold text-primary mb-8 text-center">
              {odsPage.educationTitle}
            </h2>
            {odsPage.educationText.split("\n\n").map((p, i) => (
              <p key={i} className="text-primary-dark text-lg leading-relaxed mb-5 text-center">
                {p}
              </p>
            ))}
          </Reveal>
        </div>
      </section>

      <ContactSection />
    </>
  );
}
