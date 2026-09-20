import { Link } from "react-router-dom";
import { heroContent, metodologiaHome } from "../data";
import ContactSection from "../components/ContactSection";
import Reveal from "../components/Reveal";

export default function Home() {
  return (
    <>
      {/* Hero — image left, green content box right */}
      <section className="bg-white">
        <div className="max-w-245 mx-auto px-4 py-12 md:py-16 flex flex-col md:flex-row items-stretch gap-0">
          {/* Left: student image with right-edge gradient bridge */}
          <div className="md:w-1/2 relative">
            <img
              src="/images/home_1.jpg"
              alt="Students at Five a Day"
              className="w-full h-full min-h-87.5 object-cover rounded-l-2xl md:rounded-l-2xl rounded-t-2xl md:rounded-tr-none"
            />
            {/* Subtle gradient bridging image into green panel */}
            <div className="hidden md:block absolute inset-0 bg-linear-to-r from-transparent via-transparent to-accent-green/40 rounded-l-2xl pointer-events-none" />
          </div>

          {/* Right: green content card */}
          <div className="md:w-1/2 bg-accent-green relative rounded-r-2xl md:rounded-r-2xl rounded-b-2xl md:rounded-bl-none p-8 md:p-10 flex flex-col justify-center overflow-hidden">
            {/* Decorative blobs — gently floating */}
            <div className="animate-blob absolute -top-6 -right-6 w-28 h-28 rounded-full bg-primary/20 blur-sm" />
            <div className="animate-blob-slow absolute -bottom-8 -left-8 w-32 h-32 rounded-full bg-accent-yellow/40 blur-sm" />
            <div className="animate-blob-alt absolute top-1/2 right-4 w-16 h-16 rounded-full bg-accent-blue/30 blur-sm" />

            <h1 className="font-title text-3xl md:text-4xl font-bold text-primary-darker leading-tight mb-4 relative z-10">
              {heroContent.title}{" "}
              <span className="text-primary">{heroContent.highlight}</span>
            </h1>
            <p className="text-primary-darker/90 text-base leading-relaxed mb-4 relative z-10 text-justify">
              {heroContent.description}
            </p>
            <p className="text-primary-darker/80 text-sm leading-relaxed mb-6 relative z-10 text-justify">
              {heroContent.descriptionExtended}
            </p>
            <div className="flex justify-center relative z-10">
              <a
                href="#contacto"
                className="inline-block bg-linear-to-br from-primary to-primary-dark hover:-translate-y-0.5 hover:shadow-xl hover:shadow-primary/40 text-white font-heading font-bold py-3 px-8 text-base transition-all duration-200 shadow-lg rounded-md"
              >
                {heroContent.cta}
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* Yellow banner */}
      <section id="yellow-banner" className="bg-accent-yellow py-10">
        <div className="max-w-245 mx-auto px-4 text-center">
          <p className="text-primary-darker text-lg md:text-xl font-heading font-semibold leading-relaxed">
            {heroContent.extras}
          </p>
          <p className="mt-4 text-primary-darker/80 italic text-sm">
            {heroContent.ctaSubtext}
          </p>
        </div>
      </section>

      {/* Metodología preview — alternating blocks */}
      <section className="py-16 bg-white">
        <div className="max-w-245 mx-auto px-4">
          <Reveal>
            <h2 className="font-title text-3xl md:text-4xl font-bold text-primary-dark text-center mb-4">
              {metodologiaHome.sectionTitle}
            </h2>
            <p className="text-center text-gray-600 max-w-3xl mx-auto mb-14">
              {metodologiaHome.intro}
            </p>
          </Reveal>

          <div className="space-y-20">
            {metodologiaHome.blocks.map((block, i) => (
              <Reveal key={block.title} delay={100}>
                <div
                  className={`flex flex-col ${
                    i % 2 === 0 ? "md:flex-row" : "md:flex-row-reverse"
                  } items-center gap-10`}
                >
                  {/* Image with hover zoom */}
                  <div className="flex-1 overflow-hidden rounded-xl shadow-lg group">
                    <img
                      src={block.image}
                      alt={block.title}
                      className="w-full max-h-80 object-cover transition-transform duration-500 group-hover:scale-105"
                    />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-heading text-2xl font-bold text-primary-dark mb-4">
                      {block.title}
                    </h3>
                    <p className="text-gray-700 leading-relaxed text-justify">{block.text}</p>
                    {block.link && (
                      <Link
                        to={block.link.path}
                        className="inline-block mt-5 bg-primary hover:bg-primary-dark hover:-translate-y-0.5 hover:shadow-md text-white font-heading font-semibold py-2 px-6 rounded-md transition-all duration-200"
                      >
                        {block.link.label}
                      </Link>
                    )}
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <ContactSection />
    </>
  );
}
