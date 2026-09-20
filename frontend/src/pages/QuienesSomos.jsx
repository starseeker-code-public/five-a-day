import { Fragment } from "react";
import { founders, foundersPage } from "../data";
import ContactSection from "../components/ContactSection";
import Reveal from "../components/Reveal";

export default function QuienesSomos() {
  return (
    <>
      {/* The only page whose design has no visible page heading. `sr-only`
          keeps the layout untouched while giving the document the single <h1>
          every other page already has. Swap it for a visible heading if the
          design ever grows one. */}
      <h1 className="sr-only">{foundersPage.title}</h1>

      {founders.map((founder, i) => {
        const isEven = i % 2 === 0;
        return (
          <article key={founder.id}>
            {/* Full-width two-panel header */}
            <div className={`flex flex-col ${isEven ? "md:flex-row" : "md:flex-row-reverse"}`}>

              {/* Photo panel — mint green background */}
              <div className="md:w-1/2 bg-mint relative min-h-105 md:min-h-125 overflow-hidden flex items-end justify-center">
                <img
                  src={founder.image}
                  alt={founder.name}
                  className="relative z-10 h-[95%] max-h-120 w-auto object-contain object-bottom transition-transform duration-700 hover:scale-[1.02]"
                />
              </div>

              {/* Text panel — white */}
              <div className={`md:w-1/2 bg-white flex flex-col justify-center py-12 px-10 ${isEven ? "md:pl-20 md:pr-28" : "md:pr-20 md:pl-28"}`}>
                <Reveal>
                  <p className="font-special text-lg font-semibold text-gray-800 mb-0.5">
                    {founder.name}
                  </p>
                  <p className="font-heading text-base text-primary mb-8">
                    {founder.role}
                  </p>
                  {/* Quotes — editorial treatment */}
                  <div className="relative">
                    {/* Watermark quote icon */}
                    <svg
                      className="absolute -top-5 -left-3 w-24 h-24 text-primary/10 pointer-events-none select-none"
                      fill="currentColor"
                      viewBox="0 0 24 24"
                      aria-hidden="true"
                    >
                      <path d="M14.017 21v-7.391c0-5.704 3.731-9.57 8.983-10.609l.995 2.151c-2.432.917-3.995 3.638-3.995 5.849h4v10h-9.983zm-14.017 0v-7.391c0-5.704 3.748-9.57 9-10.609l.996 2.151c-2.433.917-3.996 3.638-3.996 5.849h3.983v10h-9.983z" />
                    </svg>

                    {/* English quote — gradient text */}
                    <blockquote className="relative font-heading text-3xl md:text-4xl italic leading-snug mb-5 before:content-none after:content-none quote-text-gradient">
                      {founder.quoteEn}
                    </blockquote>

                    {/* Ornamental divider */}
                    <div className="flex items-center gap-3 mb-4">
                      <div className="flex-1 h-px divider-fade-right" />
                      <div className="w-1.5 h-1.5 rounded-full bg-forest/50" />
                      <div className="flex-1 h-px divider-fade-left" />
                    </div>

                    {/* Spanish translation */}
                    <blockquote className="font-heading text-base italic text-forest/60 text-center tracking-wide before:content-none after:content-none">
                      {founder.quoteEs}
                    </blockquote>
                  </div>
                </Reveal>
              </div>
            </div>

            {/* Bio + Experience — full width */}
            <div className="bg-white py-12">
              <div className="max-w-245 mx-auto px-8 md:px-14 space-y-10">
                <Reveal>
                  <div>
                    <h3 className="font-title text-xl font-semibold text-primary-dark mb-3">{foundersPage.aboutLabel}</h3>
                    <p className="text-gray-700 leading-relaxed text-justify">{founder.about}</p>
                  </div>
                </Reveal>

                <Reveal delay={100}>
                  <div>
                    <h3 className="font-title text-xl font-semibold text-primary-dark mb-4">
                      {foundersPage.experienceLabel}
                    </h3>
                    <div>
                      {founder.experience.map((exp, idx, arr) => (
                        <Fragment key={exp.date}>
                          {/* Entry row */}
                          <div className="flex items-start">
                            <div className="w-28 text-right pr-4 shrink-0 text-primary font-heading text-sm font-semibold leading-none pt-0.5">
                              {exp.date}
                            </div>
                            <div className="w-3 h-3 rounded-full bg-primary shrink-0 relative z-10 -ml-1.5 mt-0.5 shadow-sm shadow-primary/40" />
                            <div className="flex-1 pl-5 text-primary-darker font-heading text-sm leading-snug">
                              {exp.text}
                            </div>
                          </div>
                          {/* Connector line between entries — not after last */}
                          {idx < arr.length - 1 && (
                            <div className="flex h-8">
                              <div className="w-28 shrink-0" />
                              <div className="w-px bg-primary/30" />
                            </div>
                          )}
                        </Fragment>
                      ))}
                    </div>
                  </div>
                </Reveal>
              </div>
            </div>
          </article>
        );
      })}

      <ContactSection />
    </>
  );
}
