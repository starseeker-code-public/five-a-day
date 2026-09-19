import { faqPage } from "../data";
import ContactSection from "../components/ContactSection";
import Reveal from "../components/Reveal";

const icons = {
  "academic-cap": (
    <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 14l9-5-9-5-9 5 9 5z" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 14l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14z" />
    </svg>
  ),
  clock: (
    <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  building: (
    <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
    </svg>
  ),
  "book-open": (
    <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
    </svg>
  ),
  clipboard: (
    <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
    </svg>
  ),
  lightbulb: (
    <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m1.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
    </svg>
  ),
};

export default function PreguntasFrecuentes() {
  return (
    <>
      {/* Hero */}
      <section className="relative">
        <img
          src={faqPage.image}
          alt="Preguntas frecuentes"
          className="w-full h-48 md:h-72 object-cover"
        />
        <div className="absolute inset-0 flex items-center justify-center">
          <h1 className="font-title text-4xl md:text-5xl font-bold text-primary-dark bg-white/90 px-8 py-4 rounded-xl shadow-lg">
            {faqPage.title}
          </h1>
        </div>
      </section>

      {/* 3-column grid */}
      <section className="py-16 bg-white">
        <div className="max-w-245 mx-auto px-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {faqPage.questions.map((item, idx) => (
              <Reveal
                key={item.question}
                delay={idx * 80}
                className="bg-warm rounded-2xl p-6 shadow-sm hover:-translate-y-1.5 hover:shadow-lg transition-all duration-300 flex flex-col gap-3"
              >
                {/* Icon */}
                <div className="text-primary flex justify-center">
                  {icons[item.icon] ?? icons["lightbulb"]}
                </div>

                {/* Question */}
                <h3 className="font-heading font-bold text-primary-dark leading-snug">
                  {item.question}
                </h3>

                {/* Answer */}
                <div className="text-sm text-gray-600 leading-relaxed space-y-2">
                  {item.answer.split("\n\n").map((p, i) => (
                    <p key={i} className="text-justify">{p}</p>
                  ))}
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
