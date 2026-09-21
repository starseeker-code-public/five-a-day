/* ═══════════════════════════════════════════════════════════════
   data.js – Single Source of Truth
   ═══════════════════════════════════════════════════════════════

   This file contains ALL content and configuration for the website:
   - Text, headings, descriptions, form labels, testimonials
   - Image paths and URLs
   - Contact information and business hours
   - Navigation menu structure
   - Form field definitions

   Components import values from this file and render them.
   To change ANY text or content on the site, edit this file.

   No hardcoded content should appear in .jsx files — only references
   to exports from this data.js file.
   ═══════════════════════════════════════════════════════════════ */

export const siteConfig = {
  name: "Five a Day",
  fullName: "Five a Day English Academy",
  tagline: "We are here to learn and help our brains grow stronger.",
  taglineEs: "Estamos aquí para aprender y ayudar a nuestros cerebros a fortalecerse.",
  copyright:  "© 2026 Joaquín, Sílvia and Penélope — Built with great care, love and the best intentions",
  email: "hellofiveaday@gmail.com",
  // Mobile number used for WhatsApp and displayed as the main contact.
  phone: "613 48 11 41",
  // Landline for the academy. Used as the primary phone in the Google
  // listing data, and must match the number on Google Business Profile.
  landline: "967 04 90 96",
  whatsapp: "https://wa.me/34613481141",
  mapsUrl: "https://maps.app.goo.gl/1JMh9yrg4pPKtfJW9",
  mapsEmbedUrl: "https://maps.google.com/maps?q=38.9939218,-1.8668769&z=17&output=embed",
  mapsLinkLabel: "Abrir en Google Maps",
  address: {
    street: "C/ Hermanos Jiménez, 25",
    postalCode: "02004",
    city: "Albacete",
    country: "España",
  },
  // Exact map coordinates of the academy. Google uses these to place
  // us on the map for searches like "academia de inglés cerca de mí".
  geo: {
    latitude: 38.9939218,
    longitude: -1.8668769,
  },
  hours: {
    phone: "Por teléfono: mañanas de 12:00 a 13:30",
    inPerson: "Presencial: mañanas de 11:30 a 13:30 y tardes de 16:00 a 20:30, viernes de 12:00 a 18:30",
  },
  social: {
    instagram: "https://www.instagram.com/fiveadayenglish/",
    facebook: "https://www.facebook.com/profile.php?id=61550744760027",
  },
};

export const navigation = [
  { label: "Quiénes somos", path: "/quienes-somos" },
  { label: "Nuestra metodología", path: "/nuestra-metodologia" },
  { label: "Los ODS", path: "/ods" },
  { label: "Sobre la academia", path: "/sobre-la-academia" },
  { label: "Preguntas frecuentes", path: "/faq" },
  { label: "Contacto", path: "#contacto" },
];

export const heroContent = {
  title: "Welcome to",
  highlight: "Five a Day!",
  // Second line of the main heading. This is the single most important
  // piece of text on the site for Google: it is what tells the search
  // engine what we are and where we are. Keep the words
  // "academia de inglés" and "Albacete" in it.
  subtitle: "Academia de inglés en Albacete",
  description:
    "En Five a Day los estudiantes adquieren el inglés de manera natural y significativa mediante 5 rutinas divertidas enfocadas en la lectoescritura.",
  descriptionExtended:
    "Y no sólo aprenden inglés en nuestra academia, sino que lo hacen a la vez que desarrollan capacidades imprescindibles y realizan conexiones con la vida real, dando gran sentido a su aprendizaje.",
  cta: "¡APÚNTATE AQUÍ!",
  extras:
    "Únete a nuestra academia si quieres adquirir el inglés con una metodología increíblemente efectiva y única en toda España. ¿A qué esperas? ¡No dejes pasar esta gran oportunidad!",
  ctaSubtext: undefined,
};

export const metodologiaHome = {
  sectionTitle: "Metodología 5 a Day",
  intro:
    "Nuestra metodología 5 a Day es única en toda España. Con ella, los estudiantes adquieren la lengua inglesa de manera natural, efectiva y significativa mediante 5 rutinas divertidas basadas en la lectura y la escritura.",
  blocks: [
    {
      title: "¿Por qué la lectoescritura?",
      text: "Desde la infancia, los niños y las niñas sienten gran curiosidad por el mundo que les rodea. La lectura y la escritura de cuentos e historias de su elección permiten desarrollar otras destrezas lingüísticas y muchas capacidades esenciales, al igual que aporta mucho entretenimiento. Es simple, ¡leer y escribir es divertido!",
      image: "/images/home_2.jpg",
    },
    {
      title: "La metodología 5 a Day",
      text: "Nuestro método propio 5 a Day se inspira en las mejores prácticas internacionales en enseñanza y aprendizaje del inglés, y ha sido adaptado por nuestras fundadoras, maestras bilingües con amplia experiencia en aulas dentro y fuera de España. Ofrece un enfoque único centrado en la lectura que fomenta la autonomía, la creatividad y la confianza de cada estudiante, permitiéndoles adquirir el inglés de forma natural y activa, trabajando todas las áreas del idioma de manera divertida y significativa.",
      image: "/images/home_3.jpg",
    },
    {
      title: "Estudiantes comprometidos con su aprendizaje y el planeta",
      text: "Los 5 a Day fomentan la independencia y, a su vez, despiertan el entusiasmo y motivación del estudiante para tomar las riendas de su propio aprendizaje. Además, integramos su educación con los Objetivos de Desarrollo Sostenible. Por ello y más, esta academia va mucho más allá del mero aprendizaje de inglés.",
      link: { label: "Saber más", path: "/ods" },
      image: "/images/home_4.jpg",
    },
    {
      title: "Educando en valores",
      text: 'En Five a Day, fomentamos una serie de valores llamados "Keys to Success", ya que desarrollar buenos hábitos y valores ayuda a los estudiantes a tener éxito en todos los ámbitos de la vida. Por este motivo, les guiaremos para que desarrollen habilidades esenciales como el liderazgo, la confianza, la creatividad y la colaboración.',
      image: "/images/home_5.jpg",
    },
  ],
};

export const foundersPage = {
  // Rendered as the page's (visually hidden) <h1>. It is the one page with no
  // visible page heading — the design opens straight into the founder panels —
  // but a document still needs exactly one h1 for search engines and for anyone
  // navigating by headings with a screen reader.
  // Carries the town and the trade deliberately: it is the one <h1> on the
  // page, so it is what Google reads as the page's subject. "Quiénes somos"
  // alone said nothing a search engine could rank.
  title: "Quiénes somos: las maestras de Five a Day English Academy en Albacete",
  aboutLabel: "Sobre mí",
  experienceLabel: "Experiencia profesional",
};

export const founders = [
  {
    id: "penelope",
    name: "Ms Penélope",
    role: "Co-fundadora y maestra bilingüe",
    quoteEn:
      "As long as I know that I am doing my best, I can accept the fact that I am not always going to get it right.",
    quoteEs:
      "Mientras sepa que lo estoy haciendo lo mejor posible, puedo aceptar el hecho de que no siempre voy a hacerlo bien.",
    image: "/images/penelope.webp",
    about:
      "Me considero una persona metódica y emprendedora, que siempre ha sentido gran pasión por los idiomas, la educación y las artes. De este modo, doy lo mejor de mí para promocionar una educación de calidad a través de un aprendizaje activo, lúdico, significativo y cooperativo. Adopto una aptitud positiva y de mejora continua afirmando que \"estamos en continuo crecimiento y los errores se convierten en una valiosa oportunidad de aprendizaje\". En cuanto a mi carrera profesional, he tenido la gran oportunidad de trabajar como maestra de inglés en diferentes colegios internacionales en Malta, República Checa, Irlanda y Países Bajos. Sin duda alguna, estas experiencias me han proporcionado la habilidad de aceptar retos y adaptarme rápidamente a nuevos contextos y culturas.",
    experience: [
      { date: "Agosto 2021", text: "Maestra en Quality Schools International, Malta" },
      { date: "Agosto 2019", text: "Maestra en Escuela Internacional en Ostrava, República Checa" },
      { date: "Enero 2019", text: "Maestra en prácticas en Galway Educate Together School, Irlanda" },
      { date: "Agosto 2017", text: "Maestra en prácticas en Países Bajos" },
    ],
  },
  {
    id: "silvia",
    name: "Ms Silvia",
    role: "Co-fundadora y maestra bilingüe",
    quoteEn:
      "You must actively shape your own destiny, and the destiny of the world.",
    quoteEs:
      "Debes forjar activamente tu propio destino, y el destino del mundo.",
    image: "/images/silvia.webp",
    about:
      "En cuanto finalicé mis estudios de Educación Primaria Bilingüe en 2019, me mudé a Bélgica para trabajar en el Colegio Internacional de Lovaina como maestra de primaria en inglés. Durante los más de cuatro años que estuve residiendo en Bélgica, aprendí y consolidé por mi cuenta la metodología 5 a Day para enseñar de manera rápida y efectiva el inglés a estudiantes de diferentes procedencias, edades y niveles de inglés. Al mismo tiempo, me formé como Defensora de las Escuelas Globales de la ONU para integrar la Educación para el Desarrollo Sostenible en mis lecciones. Tanto yo como mis antiguos estudiantes hemos sido testigos de primera mano del éxito de la metodología 5 a Day y, sin lugar a dudas, hubo un gran antes y después tras implantar esta metodología en mis clases de inglés. Los numerosos beneficios de la metodología 5 a Day fueron tan evidentes que el profesorado y la dirección del colegio sintieron gran inspiración tras mi trabajo y decidieron usar a su vez este método en sus propias clases de inglés. Actualmente, estoy entusiasmada de estar de vuelta en España y de poder ayudar a más alumnas y alumnos a aprender inglés con esta metodología tan efectiva e innovadora.",
    experience: [
      {
        date: "Septiembre 2022",
        text: "Invitada como ponente en la Conferencia Internacional de Desarrollo Sostenible de la ONU para compartir su experiencia como maestra y Defensora de las Escuelas Globales",
      },
      {
        date: "Junio 2022",
        text: "Diversos medios de comunicación belgas e internacionales se hacen eco de la visita del Alcalde de Lovaina a la clase de Silvia, incluido el UNSDSN de la ONU",
      },
      {
        date: "Febrero 2022",
        text: "Finaliza con éxito el programa de Global Schools Advocates, iniciativa de la Red de Soluciones para el Desarrollo Sostenible de la ONU",
      },
      {
        date: "Agosto 2021",
        text: "Seleccionada de entre más de 2.000 educadores a nivel internacional para convertirse en Defensora de las Escuelas Globales",
      },
      {
        date: "2019 – 2022",
        text: "Maestra en International School of Leuven, Bélgica",
      },
    ],
  },
];

export const metodologiaPage = {
  title: "¿Qué ofrecemos?",
  video: "/videos/video.mp4",
  videoTitle: "Vídeo de presentación de 5 a Day",
  intro:
    "Para los más jóvenes, ofrecemos clases de inglés dos veces a la semana de 1 hora y 20 minutos de duración por sesión tanto a nivel principiante como avanzado, y todo el rango intermedio. Además, los viernes ofrecemos sesiones especiales gratuitas llamadas \"Fun Fridays\" durante las cuales se realizan actividades tan divertidas como Zumba en Inglés, talleres de manualidades o actividades dedicadas a los ODS.",
  groups:
    "Trabajamos con grupos reducidos de 8 estudiantes como máximo para garantizar una enseñanza individualizada y adaptada a las necesidades de cada estudiante, la cual es posible gracias a la metodología 5 a Day y nuestras experimentadas maestras.",
  adults:
    "Para adolescentes y adultos, ofrecemos un club de lectura en inglés y clases de conversación en grupos reducidos, para poner en práctica el idioma y mejorar tu pronunciación y confianza al hablar inglés. También ofrecemos preparación para exámenes oficiales.",
  routinesIntro:
    "A continuación, podrás ver un resumen de las 5 rutinas que componen los pilares de la metodología \"5 a Day\" y que los estudiantes llevarán a cabo durante las sesiones:",
  routines: [
    {
      title: "Read to Self",
      description:
        "Lectura independiente. Base fundamental para crear lectores, escritores y aprendices para toda la vida que estén motivados de forma independiente.",
      image: "/images/metodologia_1.jpg",
    },
    {
      title: "Read to Someone",
      description:
        "Leer con alguien. Ayuda a los lectores a desarrollar las áreas de comprensión, precisión, fluidez, atención, colaboración, etc. ¡Les encanta la lectura en pareja!",
      image: "/images/metodologia_2.jpg",
    },
    {
      title: "Listen to Reading",
      description:
        "Escucha de la lectura. Proporciona modelos de fluidez que son valiosos para todas las edades, pero especialmente para aquellos cuya comprensión auditiva supera su nivel de lectura y esencial para los que están aprendiendo inglés.",
      image: "/images/metodologia_3.jpg",
    },
    {
      title: "Work on Writing",
      description:
        "Escritura creativa. Los estudiantes disfrutan escribiendo acerca de temas de su elección, creciendo como escritores con la guía de sus profesoras.",
      image: "/images/metodologia_4.jpg",
    },
    {
      title: "Word Work",
      description:
        "Trabajo con palabras. Se experimenta con patrones ortográficos y gramaticales. Se memorizan palabras usadas con frecuencia y se desarrolla una auténtica curiosidad e interés por palabras nuevas que les permite expandir y enriquecer su vocabulario, así como escribir con corrección.",
      image: "/images/metodologia_5.jpg",
    },
  ],
  funFridays: {
    title: "Fun Fridays!",
    description:
      "Los viernes se llevan a cabo eventos especiales gratuitos de duración variable a los que podrán asistir voluntariamente los niños y niñas. Alguno ejemplos son Zumba en Inglés y talleres de manualidades.",
    image: "/images/metodologia_6.jpg",
  },
};

export const odsPage = {
  title: "Five a Day y los ODS",
  subtitle: "Conectando el aprendizaje con la vida real y el planeta",
  intro:
    "¿Conoces los Objetivos de Desarrollo Sostenible o los ODS?\n\nEl mundo necesita más de sus habitantes, especialmente de la siguiente generación, para tener el conocimiento, valores, y habilidades para navegar mejor este futuro incierto, hacer frente a sus grandes retos, y construir comunidades más prósperas y resilientes.",
  educationTitle: "Educación para el Desarrollo Sostenible",
  educationText:
    "El 25 de septiembre de 2015, los líderes mundiales adoptaron un conjunto de objetivos globales para erradicar la pobreza, proteger el planeta y asegurar la prosperidad para todos como parte de una nueva agenda de desarrollo sostenible. Nace así la Agenda 2030 con 17 ODS con metas específicas que deben alcanzarse en los próximos años.\n\nLa investigación muestra que la Educación para el Desarrollo Sostenible (EDS) es una herramienta crucial, que no sólo empodera a los estudiantes para dar forma a un mundo mejor, sino que también se desempeñan mejor académicamente.\n\n¿Sabías que más de la mitad de la población de nuestro planeta está por debajo de los 30 años? Es la mayor generación de personas jóvenes que el mundo haya visto. Esto pone a las educadoras como nosotras en una posición de influencia única para educar a millones de estudiantes a sobrepasar los grandes desafíos del siglo XXI y llevar una vida saludable y productiva, en armonía con la naturaleza.\n\nEs por ello que en nuestra academia vamos mucho más allá del inglés y ofrecemos una educación integral que forma a estudiantes independientes, apasionados por la lectoescritura, muy motivados con su propio aprendizaje, con gran creatividad e imaginación, y también ciudadanos responsables y comprometidos con el planeta y su medioambiente. Por todo lo mencionado anteriormente, Five a Day es mucho más que una academia de inglés.",
  sdgGuidelinesNote:
    "Para conocer las directrices sobre el uso del logo de los Objetivos de Desarrollo Sostenible (ODS), los 17 iconos y la ruleta de colores, descargue el manual corporativo.",
  sdgManualUrl:
    "https://www.un.org/sustainabledevelopment/wp-content/uploads/2019/01/SDG_Guidelines_AUG_2019_Final.pdf",
  sdgImage: "/images/ods.png",
};

export const sobreAcademiaPage = {
  title: "Sobre la academia",
  intro:
    "Nuestro centro en C/ Hermanos Jiménez 25 en Albacete nace como resultado del esfuerzo, trabajo, ganas e ilusión de dos maestras bilingües (¡hermanas!) con una enorme pasión por el inglés, la educación y la sostenibilidad.\n\nTenemos una gran experiencia en la enseñanza de inglés como lengua extranjera y un método propio innovador. Apostamos por una educación integral de gran calidad.",
  image: "/images/about.jpeg",
  differencesTitle: "Five a Day English Academy: mucho más que aprender inglés",
  differencesIntro:
    "En Five a Day, aprender inglés es una experiencia real, divertida y significativa. Nuestro método propio de cinco estaciones es único en España, diseñado para que niñas y niños, adolescentes y adultos desarrollen habilidades lingüísticas y personales que van mucho más allá del idioma.\n\n¿En qué se diferencia Five a Day de otras academias?",
  differences: [
    {
      title: "Sesiones más largas y efectivas",
      items: [
        "Clases de 1 hora y 20 minutos, dos veces por semana, frente a 50–60 minutos habituales en otras academias.",
        "Tiempo suficiente para inmersión real en el idioma y actividades dinámicas.",
      ],
    },
    {
      title: "Aprendizaje que continúa en casa",
      items: [
        "Plataforma de libros electrónicos nivelados en inglés para seguir practicando de manera autónoma.",
        "Fomenta responsabilidad y autonomía en el aprendizaje de cada estudiante.",
      ],
    },
    {
      title: "Metodología integral y lúdica",
      items: [
        "Cinco estaciones que combinan lectoescritura, conversación, arte, movimiento y proyectos.",
        "Desarrollo de pensamiento crítico, creatividad y curiosidad desde edades tempranas.",
      ],
    },
    {
      title: "Conciencia social y valores",
      items: [
        "Incorporamos los Objetivos de Desarrollo Sostenible (ODS) en actividades prácticas y motivadoras.",
        "Aprender inglés y al mismo tiempo reflexionar sobre el mundo que nos rodea.",
      ],
    },
    {
      title: "Ambiente familiar y seguro",
      items: [
        "Grupos reducidos y un espacio libre de juicios, donde cada estudiante crece con confianza y motivación.",
      ],
    },
  ],
  gallerySectionTitle: "Nuestra academia",
  testimonialsSectionTitle: "¿Qué opinan las familias?",
  mapSectionTitle: "Ubicación",
  mapFrameTitle: "Five a Day English Academy — Ubicación",
  gallery: [
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
  ],
  testimonials: [
    {
      text: "Tras 9 meses de curso puedo decir que es la extraescolar que por supuesto tengo claro que voy a repetir para mis dos hijas, ellas van felices a la academia, no se saltan nunca un día y han aprendido muchísimo. Vivimos cerca y por eso las apuntamos, pero hoy por hoy, si viviera en otro barrio no me importaría coger el coche para llevarlas... Es el dinero mejor invertido en la educación de mis hijos.",
      author: "Raquel G.",
    },
    {
      text: "Mis hijos han mejorado muchísimo su inglés desde que están en Five a Day. Lo mejor es verlos hablar con confianza y sin miedo a equivocarse. Las profesoras son increíbles y el ambiente es muy familiar.",
      author: "María T.",
    },
    {
      text: "El método es completamente diferente a lo que conocíamos. Los niños disfrutan cada sesión y aprenden de verdad. Se nota que hay una metodología detrás muy bien pensada. 100% recomendable.",
      author: "Carlos M.",
    },
  ],
};

export const faqPage = {
  title: "Preguntas frecuentes",
  image: "/images/questions.jpeg",
  questions: [
    {
      question: "¿Por qué aprender inglés en nuestra academia?",
      icon: "academic-cap",
      answer:
        "Porque es única en toda España, creada por maestras bilingües experimentadas y apasionadas por la educación y la sostenibilidad. Con el método 5 a Day los estudiantes adquieren el inglés de manera divertida, natural y significativa, además de muchas otras capacidades esenciales para la vida personal y académica.",
    },
    {
      question: "¿Qué horario tiene?",
      icon: "clock",
      answer:
        "El horario de apertura de nuestra academia es de lunes a jueves de 15:30h a 20:30h y los viernes de 16:00 a 18:30.\n\nPara cualquier consulta también puedes contactar con nosotras por teléfono o email mañanas y tardes, y nosotras contactaremos contigo con la mayor brevedad posible.",
    },
    {
      question: "¿Cómo son las instalaciones?",
      icon: "building",
      answer:
        "Contamos con dos aulas espaciosas completamente equipadas para que todas nuestras alumnas y alumnos puedan aprender sintiéndose cómodos y a gusto. Su diseño permite a los estudiantes interactuar entre tod@s o en pequeños grupos, de manera colaborativa y dinámica.",
    },
    {
      question: "¿Cómo son nuestras clases de inglés?",
      icon: "book-open",
      answer:
        'Trabajamos con grupos reducidos, lo cual permite proporcionar una enseñanza personalizada y adaptada a las necesidades de cada estudiante. Empleamos la metodología "5 a Day" basada en el libro "The Daily 5" redactado por maestras estadounidenses tras mucha investigación y puesta en práctica en el aula de inglés.',
    },
    {
      question: "¿Cómo inscribirse en nuestra academia?",
      icon: "clipboard",
      answer:
        "Contacta con nosotras por teléfono llamando al 967 04 90 96, escribiendo un WhatsApp al 613 48 11 41 o a través de nuestro email hellofiveaday@gmail.com.\n\n¡Te invitamos a probar una clase gratuita sin nigún tipo de compromiso! Visítanos en nuestras instalaciones en Albacete en C/ Hermanos Jiménez 25.",
    },
    {
      question: "¿Qué otras habilidades se desarrollan aquí?",
      icon: "lightbulb",
      answer:
        'Además de las habilidades básicas del inglés (comprensión y expresión tanto orales como escritas), nuestra academia educa en valores como el respeto, la cooperación, y la responsabilidad entre otros llamados "Keys to Success".',
    },
  ],
};

// Links OUT of the public site and INTO the Django application that shares
// this origin. They are plain paths Django serves, NOT React routes — which is
// why the Navbar renders them with <a href>, never <Link>/<NavLink>. A router
// link would navigate client-side, find no matching <Route>, and render a
// blank page while the URL bar showed the right address.
export const appAccess = {
  // The staff application: teachers and admins sign in here.
  staff: { label: "App", path: "/app/", title: "Acceso para el equipo de Five a Day" },

  // PARENT PORTAL — present but HIDDEN, switched by `enabled` below.
  //
  // The button is real code, not a commented-out block: it compiles, it is
  // linted, and the tests render it, so it cannot rot while it waits. What
  // keeps it off the page is one boolean, read by components/Navbar.jsx.
  //
  // It is off because `settings.PARENT_PORTAL_ENABLED` is False server-side,
  // which makes every /app/parent/ URL a 404 BY DESIGN — login and recovery
  // included, so a family holding an old link finds nothing rather than a form
  // that cannot help them. A visible button into that is a dead end carrying
  // the academy's name, which is worse than no button at all.
  //
  // TO ENABLE, once the portal is turned on server-side: set `enabled: true`.
  // That is the whole change — the markup, the styling and the mobile
  // behaviour are already in place (the purple top bar renders at every width,
  // so there is no separate mobile copy). Three tests assert it is hidden and
  // will go red, which is them doing their job:
  //   src/test/Navbar.test.jsx   "the parent portal link"
  //   src/test/data.test.js      "the parent portal entry is present but disabled"
  //   project/tests/integration/test_frontend_site.py
  //                              test_the_parent_portal_link_is_not_live
  parents: {
    enabled: false,
    label: "Padres",
    path: "/app/parent/login/",
    title: "Acceso para familias",
  },
};

export const contactForm = {
  title: "Contacta con nosotras",
  subtitle:
    "Rellena el formulario con tus datos personales y nos pondremos en contacto contigo lo antes posible.",
  successTitle: "¡Mensaje enviado correctamente!",
  successBody: "Hemos recibido tu formulario. Para una respuesta más rápida, también puedes contactarnos por WhatsApp.",
  // Shown in the confirmation dialog under successBody. It answers the one
  // question a family actually has after sending ("and now what?"), which the
  // old inline panel left unanswered.
  successFollowUp: "Te responderemos lo antes posible en el horario de atención.",
  successWhatsappLabel: "Escríbenos por WhatsApp",
  successCloseLabel: "Cerrar",
  // Seconds the send button stays disabled after a message goes through. This
  // MUST equal CONTACT_COOLDOWN_SECONDS in core/views/frontend.py — the server
  // is what actually enforces it (a script never runs this countdown), and a
  // button that re-enables early would invite a click answered with a 429.
  // `tests/integration/test_frontend_site.py` fails if the two disagree.
  cooldownSeconds: 60,
  cooldownLabel: "Espera",
  throttleMessage:
    "Acabas de enviarnos un mensaje. Espera un momento antes de enviar otro, o escríbenos por WhatsApp.",
  legalLinkLabel: "Aviso legal",
  maintenanceActive: false,
  maintenanceTitle: "Formulario en Mantenimiento",
  maintenanceMessage: "El formulario de contacto está en mantenimiento. Por favor, envíanos un mensaje por WhatsApp y te responderemos lo antes posible.",
  fields: [
    { name: "nombre", label: "Nombre", type: "text", required: true },
    { name: "apellidos", label: "Apellidos", type: "text", required: false },
    { name: "email", label: "Email", type: "email", required: true },
    { name: "telefono", label: "Teléfono", type: "tel", required: true },
    {
      name: "horario",
      label: "Horario preferente",
      type: "select",
      required: false,
      options: ["16:10", "17:40", "19:10"],
    },
    { name: "edad", label: "Edad del estudiante", type: "text", required: false },
    { name: "mensaje", label: "Déjanos un mensaje...", type: "textarea", required: true },
  ],
  submitLabel: "Enviar",
};

export const whatsappPopup = {
  question: "¿Tienes alguna pregunta?",
  body: "Escríbenos por WhatsApp y te respondemos lo antes posible. ¡Estaremos encantadas de ayudarte!",
  cta: "Enviar mensaje",
};

export const legalPage = {
  title: "Aviso Legal",
  sections: [
    {
      heading: "1. Datos identificativos",
      body: "En cumplimiento con el deber de información recogido en artículo 10 de la Ley 34/2002, de 11 de julio, de Servicios de la Sociedad de la Información y del Comercio Electrónico, la titular de este sitio web es Silvia Yubitza Moreno Carlín, con domicilio en C/ Hermanos Jiménez, 25, 02004 Albacete, España. Email: hellofiveaday@gmail.com. Teléfono: 967 049 096.",
    },
    {
      heading: "2. Propiedad intelectual",
      body: "Todos los contenidos de este sitio web, incluyendo textos, fotografías, gráficos, imágenes, iconos, tecnología, software así como su diseño gráfico y códigos fuente, constituyen una obra cuya propiedad pertenece a Five a Day English Academy.",
    },
    {
      heading: "3. Protección de datos",
      body: "De conformidad con lo establecido en el Reglamento (UE) 2016/679 del Parlamento Europeo y del Consejo (RGPD), le informamos de que los datos personales que nos proporcione a través del formulario de contacto serán tratados con la finalidad de gestionar su consulta. La base legal del tratamiento es su consentimiento. No se cederán datos a terceros salvo obligación legal.",
    },
    {
      heading: "4. Limitación de responsabilidad",
      body: "Five a Day English Academy no se hace responsable de los posibles errores de seguridad que se puedan producir ni de los posibles daños que puedan causarse al sistema informático del usuario (hardware y software), los ficheros o documentos almacenados en el mismo, como consecuencia de un mal funcionamiento del navegador o del uso de versiones no actualizadas del mismo.",
    },
  ],
};
