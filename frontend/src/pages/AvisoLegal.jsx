import { legalPage } from "../data";

export default function AvisoLegal() {
  return (
    <section className="py-16 bg-white">
      <div className="max-w-[980px] mx-auto px-4">
        <h1 className="font-heading text-3xl font-bold text-primary-dark mb-8">
          {legalPage.title}
        </h1>

        <div className="prose prose-gray max-w-none space-y-4 text-gray-700 leading-relaxed">
          {legalPage.sections.map((section) => (
            <div key={section.heading}>
              <h2 className="text-xl font-semibold text-primary-dark">
                {section.heading}
              </h2>
              <p>{section.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
