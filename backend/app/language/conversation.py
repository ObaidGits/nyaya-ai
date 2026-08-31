"""Multilingual conversational short-circuit (D-077).

Same contract as the English ``conversational_reply`` layer (D-067/D-068),
extended to the supported Indian languages: exact social formulas and
anchored whole-message identity/capability questions receive a fixed,
code-produced reply in the target language — no retrieval, no translation
call, no LLM call, no citations. Every legal, ambiguous, or
injection-style message in any language falls through to the grounded RAG
pipeline.

Replies are fixed strings (product copy, not legal content). They make no
legal claim and assert no runtime state. Language coverage of each category
is conservative on purpose: a missing pattern falls through to RAG (the
pre-feature behavior), never a false interception.
"""

from __future__ import annotations

import re

from app.language.models import LanguageCode

# Category names are shared with app.generation.conversation.
CATEGORIES = ("greeting", "thanks", "farewell", "ack", "how_are_you", "identity", "capability")

# Exact normalized phrases per language. Additions must remain single
# social formulas — never phrases that could carry a legal question.
_INDIC_PHRASES: dict[LanguageCode, dict[str, frozenset[str]]] = {
    LanguageCode.HI: {
        "greeting": frozenset({"नमस्ते", "नमस्कार", "हैलो", "सुप्रभात", "शुभ संध्या"}),
        "thanks": frozenset({"धन्यवाद", "बहुत धन्यवाद", "शुक्रिया", "आभार"}),
        "farewell": frozenset({"अलविदा", "फिर मिलेंगे", "शुभ रात्रि"}),
        "ack": frozenset({"ठीक", "ठीक है", "हाँ", "जी", "नहीं", "समझ गया", "अच्छा"}),
    },
    LanguageCode.MR: {
        "greeting": frozenset({"नमस्कार", "नमस्ते", "हॅलो", "शुभ प्रभात"}),
        "thanks": frozenset({"धन्यवाद", "खूप धन्यवाद"}),
        "farewell": frozenset({"पुन्हा भेटूया"}),
        "ack": frozenset({"ठीक आहे", "हो", "नाही", "समजले"}),
    },
    LanguageCode.BN: {
        "greeting": frozenset({"নমস্কার", "হ্যালো", "সুপ্রভাত", "শুভ সন্ধ্যা"}),
        "thanks": frozenset({"ধন্যবাদ", "অনেক ধন্যবাদ"}),
        "farewell": frozenset({"বিদায়", "আবার দেখা হবে", "শুভ রাত্রি"}),
        "ack": frozenset({"ঠিক আছে", "হ্যাঁ", "না", "বুঝেছি"}),
    },
    LanguageCode.AS: {
        "greeting": frozenset({"নমস্কাৰ", "নমস্তে"}),
        "thanks": frozenset({"ধন্যবাদ"}),
        "farewell": frozenset({"বিদায়"}),
        "ack": frozenset({"ঠিক আছে", "হয়", "নহয়", "বুজিলো"}),
    },
    LanguageCode.GU: {
        "greeting": frozenset({"નમસ્તે", "નમસ્કાર", "હેલો", "શુભ સવાર"}),
        "thanks": frozenset({"આભાર", "ખૂબ આભાર"}),
        "farewell": frozenset({"આવજો", "ફરી મળીશું"}),
        "ack": frozenset({"ઠીક છે", "હા", "ના", "સમજાઈ ગયું"}),
    },
    LanguageCode.TA: {
        "greeting": frozenset({"வணக்கம்", "ஹலோ", "காலை வணக்கம்"}),
        "thanks": frozenset({"நன்றி", "மிக்க நன்றி"}),
        "farewell": frozenset({"போய் வருகிறேன்", "மீண்டும் சந்திப்போம்"}),
        "ack": frozenset({"சரி", "ஆம்", "இல்லை", "புரிந்தது"}),
    },
    LanguageCode.TE: {
        "greeting": frozenset({"నమస్కారం", "నమస్తే", "హలో"}),
        "thanks": frozenset({"ధన్యవాదాలు", "చాలా ధన్యవాదాలు"}),
        "farewell": frozenset({"వెళ్ళిపోతున్నా", "మళ్ళీ కలుద్దాం"}),
        "ack": frozenset({"సరే", "అవును", "కాదు", "అర్థమైంది"}),
    },
    LanguageCode.KN: {
        "greeting": frozenset({"ನಮಸ್ಕಾರ", "ನಮಸ್ತೆ", "ಹಲೋ"}),
        "thanks": frozenset({"ಧನ್ಯವಾದ", "ತುಂಬಾ ಧನ್ಯವಾದ"}),
        "farewell": frozenset({"ವಿದಾಯ", "ಮತ್ತೆ ಸಿಗೋಣ"}),
        "ack": frozenset({"ಸರಿ", "ಹೌದು", "ಇಲ್ಲ", "ಅರ್ಥವಾಯಿತು"}),
    },
    LanguageCode.ML: {
        "greeting": frozenset({"നമസ്കാരം", "ഹലോ"}),
        "thanks": frozenset({"നന്ദി", "വളരെ നന്ദി"}),
        "farewell": frozenset({"വിട", "വീണ്ടും കാണാം"}),
        "ack": frozenset({"ശരി", "അതെ", "അല്ല", "മനസ്സിലായി"}),
    },
    LanguageCode.PA: {
        "greeting": frozenset({"ਸਤ ਸ੍ਰੀ ਅਕਾਲ", "ਨਮਸਤੇ", "ਹੈਲੋ"}),
        "thanks": frozenset({"ਧੰਨਵਾਦ", "ਬਹੁਤ ਧੰਨਵਾਦ"}),
        "farewell": frozenset({"ਅਲਵਿਦਾ", "ਫਿਰ ਮਿਲਾਂਗੇ"}),
        "ack": frozenset({"ਠੀਕ", "ਹਾਂ", "ਨਹੀਂ", "ਸਮਝ ਗਿਆ"}),
    },
    LanguageCode.OR: {
        "greeting": frozenset({"ନମସ୍କାର", "ହେଲୋ"}),
        "thanks": frozenset({"ଧନ୍ୟବାଦ", "ବହୁତ ଧନ୍ୟବାଦ"}),
        "farewell": frozenset({"ବିଦାୟ"}),
        "ack": frozenset({"ଠିକ୍ ଅଛି", "ହଁ", "ନା", "ବୁଝିଗଲି"}),
    },
}

# Anchored whole-message patterns. Any additional content disqualifies.
_TAIL = r"[\s?!.।॥]*$"
_INDIC_PATTERNS: dict[LanguageCode, list[tuple[re.Pattern[str], str]]] = {
    LanguageCode.HI: [
        (re.compile(r"^आप(?: कौन)? (?:कौन|हो|हैं)" + _TAIL), "identity"),
        (re.compile(r"^(?:तुम|तू) कौन (?:हो|है)" + _TAIL), "identity"),
        (re.compile(r"^(?:आपका|तुम्हारा|तेरा) नाम क्या है" + _TAIL), "identity"),
        (re.compile(r"^न्याय क्या (?:है|हैं)" + _TAIL), "identity"),
        (re.compile(r"^आप क्या कर (?:सकते|सकतीं) हैं" + _TAIL), "capability"),
        (re.compile(r"^आप कैसे मदद कर (?:सकते|सकतीं) हैं" + _TAIL), "capability"),
        (re.compile(r"^आप कैसे हैं" + _TAIL), "how_are_you"),
    ],
    LanguageCode.MR: [
        (re.compile(r"^(?:तुम्ही|तू) कोण (?:आहात|आहेस)" + _TAIL), "identity"),
        (re.compile(r"^(?:तुमचे|तुझे) नाव काय" + _TAIL), "identity"),
        (re.compile(r"^तुम्ही काय करू शकता" + _TAIL), "capability"),
    ],
    LanguageCode.BN: [
        (re.compile(r"^(?:আপনি|তুমি|তোরা) কে" + _TAIL), "identity"),
        (re.compile(r"^(?:আপনার|তোমার) নাম কি" + _TAIL), "identity"),
        (re.compile(r"^ন্যায় কি" + _TAIL), "identity"),
        (re.compile(r"^(?:আপনি|তুমি) কি করতে পারেন" + _TAIL), "capability"),
        (re.compile(r"^(?:আপনি|তুমি) কেমন আছেন" + _TAIL), "how_are_you"),
    ],
    LanguageCode.AS: [
        (re.compile(r"^(?:আপুনি|তুমি|তই) কোন" + _TAIL), "identity"),
        (re.compile(r"^(?:আপোনাৰ|তোমাৰ) নাম কি" + _TAIL), "identity"),
        (re.compile(r"^আপুনি কি কৰিব পাৰে" + _TAIL), "capability"),
    ],
    LanguageCode.GU: [
        (re.compile(r"^(?:તમે|તું) કોણ (?:છો|છે)" + _TAIL), "identity"),
        (re.compile(r"^(?:તમારું|તારું) નામ શું છે" + _TAIL), "identity"),
        (re.compile(r"^તમે શું કરી શકો છો" + _TAIL), "capability"),
    ],
    LanguageCode.TA: [
        (re.compile(r"^(?:நீங்கள்|நீ) யார்" + _TAIL), "identity"),
        (re.compile(r"^(?:உங்கள்|உன்) பெயர் என்ன" + _TAIL), "identity"),
        (re.compile(r"^(?:நீங்கள்|நீ) என்ன செய்ய முடியும்" + _TAIL), "capability"),
    ],
    LanguageCode.TE: [
        (re.compile(r"^(?:మీరు|నువ్వు|నువ్వు) ఎవరు" + _TAIL), "identity"),
        (re.compile(r"^(?:మీ|నీ) పేరు ఏమిటి" + _TAIL), "identity"),
        (re.compile(r"^(?:మీరు|నువ్వు) ఏమి చేయగలరు" + _TAIL), "capability"),
    ],
    LanguageCode.KN: [
        (re.compile(r"^(?:ನೀವು|ನೀನು) ಯಾರು" + _TAIL), "identity"),
        (re.compile(r"^(?:ನಿಮ್ಮ|ನಿನ್ನ) ಹೆಸರು ಏನು" + _TAIL), "identity"),
        (re.compile(r"^(?:ನೀವು|ನೀನು) ಏನು ಮಾಡಬಲ್ಲಿರಿ" + _TAIL), "capability"),
    ],
    LanguageCode.ML: [
        (re.compile(r"^(?:നിങ്ങൾ|നീ) ആരാണ്" + _TAIL), "identity"),
        (re.compile(r"^(?:നിങ്ങളുടെ|നിന്റെ) പേര് എന്താണ്" + _TAIL), "identity"),
        (re.compile(r"^(?:നിങ്ങൾക്ക്|നിനക്ക്) എന്ത് ചെയ്യാൻ കഴിയും" + _TAIL), "capability"),
    ],
    LanguageCode.PA: [
        (re.compile(r"^(?:ਤੁਸੀਂ|ਤੂੰ) ਕੌਣ (?:ਹੋ|ਹੈ)" + _TAIL), "identity"),
        (re.compile(r"^(?:ਤੁਹਾਡਾ|ਤੇਰਾ) ਨਾਮ ਕੀ ਹੈ" + _TAIL), "identity"),
        (re.compile(r"^(?:ਤੁਸੀਂ|ਤੂੰ) ਕੀ ਕਰ ਸਕਦੇ ਹੋ" + _TAIL), "capability"),
    ],
    LanguageCode.OR: [
        (re.compile(r"^(?:ଆପଣ|ତୁମେ) କିଏ" + _TAIL), "identity"),
        (re.compile(r"^(?:ଆପଣଙ୍କ|ତୁମର) ନାମ କଣ" + _TAIL), "identity"),
        (re.compile(r"^(?:ଆପଣ|ତୁମେ) କଣ କରିପାରନ୍ତି" + _TAIL), "capability"),
    ],
}

# Fixed replies per language. Product copy: no legal claim, no runtime
# state, no capability beyond what the product is by construction.
_INDIC_REPLIES: dict[LanguageCode, dict[str, str]] = {
    LanguageCode.HI: {
        "greeting": "नमस्ते! मैं न्याय हूँ। कानून के बारे में प्रश्न पूछें, या कोई कानूनी दस्तावेज़ अपलोड करके उसके बारे में पूछें।",
        "thanks": "आपका स्वागत है! कानून पर कोई भी अन्य प्रश्न पूछ सकते हैं।",
        "farewell": "अलविदा! कानून के किसी भी प्रश्न के साथ वापस आएँ।",
        "ack": "समझ गया। जब तैयार हों, कानून पर प्रश्न पूछें।",
        "how_are_you": "मैं यहाँ हूँ और तैयार हूँ। कानून पर कोई भी प्रश्न पूछिए।",
        "identity": (
            "मैं न्याय हूँ, भारतीय आपराधिक कानून के प्रश्नों के लिए एक AI कानूनी सहायक। "
            "मैं केवल मुझे दी गई कानूनी स्रोत सामग्री से उत्तर देता हूँ और जिन अंशों को उद्धृत "
            "करता हूँ उनका सटीक उल्लेख करता हूँ। मैं वकील नहीं हूँ, और यह कानूनी सलाह नहीं है।"
        ),
        "capability": (
            "मैं अनुक्रमित स्रोत सामग्री से कानून के प्रश्नों के उत्तर दे सकता हूँ, उद्धृत अंशों "
            "का सटीक उल्लेख करते हुए, और आपके द्वारा अपलोड किए गए दस्तावेज़ के बारे में प्रश्नों के "
            "उत्तर दे सकता हूँ। यदि सामग्री में उत्तर नहीं है, तो मैं ऐसा ही कह देता हूँ। "
            "मैं वकील नहीं हूँ, और यह कानूनी सलाह नहीं है।"
        ),
    },
    LanguageCode.MR: {
        "greeting": "नमस्कार! मी न्याय आहे. कायद्याविषयी विचारा, किंवा कायदेशीर दस्तऐवज अपलोड करून त्याविषयी विचारा.",
        "thanks": "धन्यवाद! कायद्याविषयी आणखी प्रश्न विचारा.",
        "farewell": "पुन्हा भेटूया! कायद्याचा कोणताही प्रश्न घेऊन या.",
        "ack": "समजले. तयार असाल तेव्हा कायद्याविषयी विचारा.",
        "identity": (
            "मी न्याय आहे, भारतीय फौजदारी कायद्याच्या प्रश्नांसाठी AI कायदेशीर सहाय्यक. "
            "मी फक्त मला दिलेल्या कायदेशीर स्रोत सामग्रीतून उत्तर देतो आणि उतारा दर्शवतो. "
            "मी वकील नाही आणि हे कायदेशीर सल्ला नाही."
        ),
        "capability": (
            "मी नोंदवलेल्या स्रोत सामग्रीतून कायद्याच्या प्रश्नांची उत्तरे देऊ शकतो, "
            "उतारे सोबत, आणि तुम्ही अपलोड केलेल्या दस्तऐवजाविषयी उत्तरे देऊ शकतो. "
            "सामग्रीत उत्तर नसेल तर मी तसे सांगतो. मी वकील नाही आणि हे कायदेशीर सल्ला नाही."
        ),
    },
    LanguageCode.BN: {
        "greeting": "নমস্কার! আমি ন্যায়। আইন সম্পর্কে প্রশ্ন করুন, বা একটি আইনি দলিল আপলোড করে সে সম্পর্কে প্রশ্ন করুন।",
        "thanks": "স্বাগতম! আইন নিয়ে আরেকটি প্রশ্ন করুন।",
        "farewell": "বিদায়! আইনের কোনো প্রশ্ন নিয়ে আবার ফিরে আসুন।",
        "ack": "বুঝেছি। প্রস্তুত হলে আইন নিয়ে প্রশ্ন করুন।",
        "how_are_you": "আমি এখানে আছি এবং প্রস্তুত। আইন নিয়ে যেকোনো প্রশ্ন করুন।",
        "identity": (
            "আমি ন্যায়, ভারতীয় ফৌজদারি আইনের প্রশ্নের জন্য একজন AI আইনি সহকারী। "
            "আমি কেবল আমাকে দেওয়া আইনি উৎস উপকরণ থেকে উত্তর দিই এবং যে ধারার অংশ উদ্ধৃত করি "
            "তার সঠিক উল্লেখ করি। আমি আইনজীবী নই, এবং এটি আইনি পরামর্শ নয়।"
        ),
        "capability": (
            "আমি সূচিকৃত উৎস উপকরণ থেকে আইনের প্রশ্নের উত্তর দিতে পারি, ধারার সঠিক উল্লেখসহ, "
            "এবং আপনার আপলোড করা দলিল সম্পর্কে প্রশ্নের উত্তর দিতে পারি। উপকরণে উত্তর না থাকলে "
            "আমি তা বলি। আমি আইনজীবী নই, এবং এটি আইনি পরামর্শ নয়।"
        ),
    },
    LanguageCode.AS: {
        "greeting": "নমস্কাৰ! মই ন্যায়। আইনৰ বিষয়ে সুধিব, নাইবা এখন আইনি দস্তাবেজ আপলোড কৰি সেই বিষয়ে সুধিব।",
        "thanks": "ধন্যবাদ! আইনৰ বিষয়ে আৰু এটা প্ৰশ্ন সুধিব।",
        "farewell": "বিদায়! আইনৰ যিকোনো প্ৰশ্ন লৈ আকৌ আহিব।",
        "ack": "বুজিলোঁ। সাজু হ’লে আইনৰ বিষয়ে সুধিব।",
        "identity": (
            "মই ন্যায়, ভাৰতীয় ফৌজদাৰি আইনৰ প্ৰশ্নৰ বাবে এজন AI আইনি সহায়ক। "
            "মই কেৱল মোক দিয়া আইনি উৎস সামগ্ৰীৰ পৰা উত্তৰ দিওঁ। মই উকীল নহয়, "
            "আৰু এইটো আইনি পৰামৰ্শ নহয়।"
        ),
        "capability": (
            "মই সূচীভুক্ত উৎস সামগ্ৰীৰ পৰা আইনৰ প্ৰশ্নৰ উত্তৰ দিব পাৰোঁ, আৰু আপুনি আপলোড "
            "কৰা দস্তাবেজৰ বিষয়ে প্ৰশ্নৰ উত্তৰ দিব পাৰোঁ। মই উকীল নহয়, আৰু এইটো আইনি পৰামৰ্শ নহয়।"
        ),
    },
    LanguageCode.GU: {
        "greeting": "નમસ્તે! હું ન્યાય છું. કાયદા વિશે પૂછો, અથવા કાનૂની દસ્તાવેજ અપલોડ કરીને તેના વિશે પૂછો.",
        "thanks": "આભાર! કાયદા વિશે બીજો કોઈ પ્રશ્ન પૂછો.",
        "farewell": "આવજો! કાયદાના કોઈપણ પ્રશ્ન સાથે ફરી આવજો.",
        "ack": "સમજાઈ ગયું. તૈયાર હો ત્યારે કાયદા વિશે પૂછો.",
        "identity": (
            "હું ન્યાય છું, ભારતીય ફોજદારી કાયદાના પ્રશ્નો માટે AI કાનૂની સહાયક. "
            "હું મને આપેલા કાનૂની સ્રોત સામગ્રીમાંથી જ ઉત્તર આપું છું. હું વકીલ નથી, "
            "અને આ કાનૂની સલાહ નથી."
        ),
        "capability": (
            "હું અનુક્રમિત સ્રોત સામગ્રીમાંથી કાયદાના પ્રશ્નોના ઉત્તર આપી શકું છું, "
            "અને તમે અપલોડ કરેલા દસ્તાવેજ વિશે પ્રશ્નોના ઉત્તર આપી શકું છું. "
            "હું વકીલ નથી, અને આ કાનૂની સલાહ નથી."
        ),
    },
    LanguageCode.TA: {
        "greeting": "வணக்கம்! நான் நியாயா. சட்டம் பற்றி கேளுங்கள், அல்லது ஒரு சட்ட ஆவணத்தை பதிவேற்றி அதைப் பற்றி கேளுங்கள்.",
        "thanks": "நன்றி! சட்டம் குறித்து மற்றொரு கேள்வியை கேளுங்கள்.",
        "farewell": "போய் வருகிறேன்! சட்டம் தொடர்பான எந்தக் கேள்வியுடனும் மீண்டும் வாருங்கள்.",
        "ack": "புரிந்தது. தயாரானதும் சட்டம் பற்றி கேளுங்கள்.",
        "identity": (
            "நான் நியாயா, இந்திய குற்றவியல் சட்டம் குறித்த கேள்விகளுக்கான AI சட்ட உதவியாளர். "
            "எனக்கு கொடுக்கப்பட்ட சட்ட மூலப் பொருளிலிருந்து மட்டுமே நான் பதில் அளிக்கிறேன். "
            "நான் வழக்கறிஞர் அல்ல, இது சட்ட ஆலோசனை அல்ல."
        ),
        "capability": (
            "அட்டவணைப்படுத்தப்பட்ட மூலப் பொருளிலிருந்து சட்டக் கேள்விகளுக்கு பதிலளிக்க முடியும், "
            "மேலும் நீங்கள் பதிவேற்றிய ஆவணம் குறித்த கேள்விகளுக்கும் பதிலளிக்க முடியும். "
            "நான் வழக்கறிஞர் அல்ல, இது சட்ட ஆலோசனை அல்ல."
        ),
    },
    LanguageCode.TE: {
        "greeting": "నమస్కారం! నేను న్యాయ. చట్టం గురించి అడగండి, లేదా చట్టపరమైన పత్రాన్ని అప్‌లోడ్ చేసి దాని గురించి అడగండి.",
        "thanks": "ధన్యవాదాలు! చట్టం గురించి మరొక ప్రశ్న అడగండి.",
        "farewell": "మళ్ళీ కలుద్దాం! చట్టం గురించి ఏ ప్రశ్నతోనైనా తిరిగి రండి.",
        "ack": "అర్థమైంది. సిద్ధంగా ఉన్నప్పుడు చట్టం గురించి అడగండి.",
        "identity": (
            "నేను న్యాయ, భారతీయ క్రిమినల్ చట్టం ప్రశ్నల కోసం AI చట్టపరమైన సహాయకుడిని. "
            "నాకు ఇచ్చిన చట్టపరమైన మూల సామగ్రి నుండి మాత్రమే నేను సమాధానం ఇస్తాను. "
            "నేను న్యాయవాదిని కాదు, ఇది చట్టపరమైన సలహా కాదు."
        ),
        "capability": (
            "సూచీకృత మూల సామగ్రి నుండి చట్ట ప్రశ్నలకు సమాధానం చెప్పగలను, "
            "మరియు మీరు అప్‌లోడ్ చేసిన పత్రం గురించి ప్రశ్నలకు సమాధానం చెప్పగలను. "
            "నేను న్యాయవాదిని కాదు, ఇది చట్టపరమైన సలహా కాదు."
        ),
    },
    LanguageCode.KN: {
        "greeting": "ನಮಸ್ಕಾರ! ನಾನು ನ್ಯಾಯ. ಕಾನೂನಿನ ಬಗ್ಗೆ ಕೇಳಿ, ಅಥವಾ ಕಾನೂನು ದಾಖಲೆಯನ್ನು ಅಪ್‌ಲೋಡ್ ಮಾಡಿ ಅದರ ಬಗ್ಗೆ ಕೇಳಿ.",
        "thanks": "ಧನ್ಯವಾದ! ಕಾನೂನಿನ ಬಗ್ಗೆ ಇನ್ನೊಂದು ಪ್ರಶ್ನೆ ಕೇಳಿ.",
        "farewell": "ವಿದಾಯ! ಕಾನೂನಿನ ಯಾವುದೇ ಪ್ರಶ್ನೆಯೊಂದಿಗೆ ಮತ್ತೆ ಬನ್ನಿ.",
        "ack": "ಅರ್ಥವಾಯಿತು. ಸಿದ್ಧರಾದಾಗ ಕಾನೂನಿನ ಬಗ್ಗೆ ಕೇಳಿ.",
        "identity": (
            "ನಾನು ನ್ಯಾಯ, ಭಾರತೀಯ ಆಪರಾಧಿಕ ಕಾನೂನಿನ ಪ್ರಶ್ನೆಗಳಿಗಾಗಿ AI ಕಾನೂನು ಸಹಾಯಕ. "
            "ನನಗೆ ನೀಡಲಾದ ಕಾನೂನು ಮೂಲ ಸಾಮಗ್ರಿಯಿಂದ ಮಾತ್ರ ನಾನು ಉತ್ತರಿಸುತ್ತೇನೆ. "
            "ನಾನು ವಕೀಲಲ್ಲ, ಇದು ಕಾನೂನು ಸಲಹೆಯಲ್ಲ."
        ),
        "capability": (
            "ಸೂಚ್ಯಂಕಗೊಳಿಸಿದ ಮೂಲ ಸಾಮಗ್ರಿಯಿಂದ ಕಾನೂನು ಪ್ರಶ್ನೆಗಳಿಗೆ ಉತ್ತರಿಸಬಲ್ಲೆ, "
            "ಮತ್ತು ನೀವು ಅಪ್‌ಲೋಡ್ ಮಾಡಿದ ದಾಖಲೆಯ ಬಗ್ಗೆ ಪ್ರಶ್ನೆಗಳಿಗೆ ಉತ್ತರಿಸಬಲ್ಲೆ. "
            "ನಾನು ವಕೀಲಲ್ಲ, ಇದು ಕಾನೂನು ಸಲಹೆಯಲ್ಲ."
        ),
    },
    LanguageCode.ML: {
        "greeting": "നമസ്കാരം! ഞാൻ ന്യായ ആണ്. നിയമത്തെക്കുറിച്ച് ചോദിക്കുക, അല്ലെങ്കിൽ ഒരു നിയമ രേഖ അപ്‌ലോഡ് ചെയ്ത് അതിനെക്കുറിച്ച് ചോദിക്കുക.",
        "thanks": "നന്ദി! നിയമത്തെക്കുറിച്ച് മറ്റൊരു ചോദ്യം ചോദിക്കാം.",
        "farewell": "വീണ്ടും കാണാം! നിയമത്തെക്കുറിച്ചുള്ള ഏത് ചോദ്യവുമായി വീണ്ടും വരിക.",
        "ack": "മനസ്സിലായി. തയ്യാറാകുമ്പോൾ നിയമത്തെക്കുറിച്ച് ചോദിക്കുക.",
        "identity": (
            "ഞാൻ ന്യായ ആണ്, ഇന്ത്യൻ ക്രിമിനൽ നിയമത്തെക്കുറിച്ചുള്ള ചോദ്യങ്ങൾക്കുള്ള ഒരു AI "
            "നിയമ സഹായി. എനിക്ക് നൽകിയ നിയമ മൂല സാമഗ്രിയിൽ നിന്ന് മാത്രമേ ഞാൻ ഉത്തരം "
            "നൽകൂ. ഞാൻ അഭിഭാഷകൻ അല്ല, ഇത് നിയമ ഉപദേശമല്ല."
        ),
        "capability": (
            "സൂചികവൽക്കരിച്ച മൂല സാമഗ്രിയിൽ നിന്ന് നിയമ ചോദ്യങ്ങൾക്ക് ഉത്തരം നൽകാനും, "
            "നിങ്ങൾ അപ്‌ലോഡ് ചെയ്ത രേഖയെക്കുറിച്ചുള്ള ചോദ്യങ്ങൾക്ക് ഉത്തരം നൽകാനും എനിക്ക് "
            "കഴിയും. ഞാൻ അഭിഭാഷകൻ അല്ല, ഇത് നിയമ ഉപദേശമല്ല."
        ),
    },
    LanguageCode.PA: {
        "greeting": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਨਿਆਇਆ ਹਾਂ। ਕਾਨੂੰਨ ਬਾਰੇ ਪੁੱਛੋ, ਜਾਂ ਕੋਈ ਕਾਨੂੰਨੀ ਦਸਤਾਵੇਜ਼ ਅੱਪਲੋਡ ਕਰਕੇ ਉਸ ਬਾਰੇ ਪੁੱਛੋ।",
        "thanks": "ਧੰਨਵਾਦ! ਕਾਨੂੰਨ ਬਾਰੇ ਹੋਰ ਕੋਈ ਸਵਾਲ ਪੁੱਛੋ।",
        "farewell": "ਅਲਵਿਦਾ! ਕਾਨੂੰਨ ਦੇ ਕਿਸੇ ਵੀ ਸਵਾਲ ਨਾਲ ਮੁੜ ਆਓ।",
        "ack": "ਸਮਝ ਗਿਆ। ਜਦੋਂ ਤਿਆਰ ਹੋਵੋ, ਕਾਨੂੰਨ ਬਾਰੇ ਪੁੱਛੋ।",
        "identity": (
            "ਮੈਂ ਨਿਆਇਆ ਹਾਂ, ਭਾਰਤੀ ਦੰਡ ਕਾਨੂੰਨ ਦੇ ਸਵਾਲਾਂ ਵਾਸਤੇ ਇੱਕ AI ਕਾਨੂੰਨੀ ਸਹਾਇਕ। "
            "ਮੈਂ ਸਿਰਫ਼ ਮੈਨੂੰ ਦਿੱਤੀ ਗਈ ਕਾਨੂੰਨੀ ਸਰੋਤ ਸਮੱਗਰੀ ਤੋਂ ਹੀ ਜਵਾਬ ਦਿੰਦਾ ਹਾਂ। "
            "ਮੈਂ ਵਕੀਲ ਨਹੀਂ ਹਾਂ, ਅਤੇ ਇਹ ਕਾਨੂੰਨੀ ਸਲਾਹ ਨਹੀਂ ਹੈ।"
        ),
        "capability": (
            "ਮੈਂ ਤਿਆਰ ਸਰੋਤ ਸਮੱਗਰੀ ਤੋਂ ਕਾਨੂੰਨ ਦੇ ਸਵਾਲਾਂ ਦੇ ਜਵਾਬ ਦੇ ਸਕਦਾ ਹਾਂ, "
            "ਅਤੇ ਤੁਹਾਡੇ ਅੱਪਲੋਡ ਕੀਤੇ ਦਸਤਾਵੇਜ਼ ਬਾਰੇ ਸਵਾਲਾਂ ਦੇ ਜਵਾਬ ਦੇ ਸਕਦਾ ਹਾਂ। "
            "ਮੈਂ ਵਕੀਲ ਨਹੀਂ ਹਾਂ, ਅਤੇ ਇਹ ਕਾਨੂੰਨੀ ਸਲਾਹ ਨਹੀਂ ਹੈ।"
        ),
    },
    LanguageCode.OR: {
        "greeting": "ନମସ୍କାର! ମୁଁ ନ୍ୟାୟ। ଆଇନ ବିଷୟରେ ପଚାରନ୍ତୁ, କିମ୍ବା ଏକ ଆଇନଗତ ଦଲିଲ ଅପଲୋଡ୍ କରି ଏହା ବିଷୟରେ ପଚାରନ୍ତୁ।",
        "thanks": "ଧନ୍ୟବାଦ! ଆଇନ ବିଷୟରେ ଅନ୍ୟ ଏକ ପ୍ରଶ୍ନ ପଚାରନ୍ତୁ।",
        "farewell": "ବିଦାୟ! ଆଇନର ଯେକୌଣସି ପ୍ରଶ୍ନ ନେଇ ପୁଣି ଆସନ୍ତୁ।",
        "ack": "ବୁଝିଗଲି। ପ୍ରସ୍ତୁତ ହେଲେ ଆଇନ ବିଷୟରେ ପଚାରନ୍ତୁ।",
        "identity": (
            "ମୁଁ ନ୍ୟାୟ, ଭାରତୀୟ ଫୌଜଦାରୀ ଆଇନର ପ୍ରଶ୍ନ ପାଇଁ ଏକ AI ଆଇନଗତ ସହାୟକ। "
            "ମୁଁ କେବଳ ମୋତେ ଦିଆଯାଇଥିବା ଆଇନଗତ ମୂଳ ସାମଗ୍ରୀରୁ ଉତ୍ତର ଦିଏଁ। "
            "ମୁଁ ଓକିଲ ନୁହେଁ, ଏବଂ ଏହା ଆଇନଗତ ପରାମର୍ଶ ନୁହେଁ।"
        ),
        "capability": (
            "ମୁଁ ଅନୁକ୍ରମିତ ମୂଳ ସାମଗ୍ରୀରୁ ଆଇନ ପ୍ରଶ୍ନର ଉତ୍ତର ଦେଇପାରିବି, "
            "ଏବଂ ଆପଣ ଅପଲୋଡ୍ କରିଥିବା ଦଲିଲ ବିଷୟରେ ପ୍ରଶ୍ନର ଉତ୍ତର ଦେଇପାରିବି। "
            "ମୁଁ ଓକିଲ ନୁହେଁ, ଏବଂ ଏହା ଆଇନଗତ ପରାମର୍ଶ ନୁହେଁ।"
        ),
    },
}

# Strip common punctuation (Latin plus Indic danda) before matching.
_STRIP_CHARS = " \t\r\n.,!?;:'\"()-_।॥?"
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(message: str) -> str:
    return _WHITESPACE_RE.sub(" ", message.lower()).strip().strip(_STRIP_CHARS).strip()


def _leading_greeting_stripped(normalized: str) -> str:
    """Remove a leading social formula ("नमस्ते, ...", "வணக்கம், ...").

    The prefix is itself conversational and can carry no legal content, so
    "नमस्ते, आप कौन हैं?" still matches the whole-message identity pattern
    instead of falling through to RAG (live-audit regression).
    """
    for phrases_by_category in _INDIC_PHRASES.values():
        for phrase in phrases_by_category.get("greeting", frozenset()):
            if normalized.startswith(phrase):
                return normalized[len(phrase) :].lstrip(" ,.!?।॥")
    return normalized


def multilingual_category(message: str) -> str | None:
    """Match a conversational category across all supported languages.

    Returns None for anything that is not a verbatim social formula or a
    whole-message identity/capability question — including every legal
    question and injection payload, in any language.
    """
    normalized = _normalize(message)
    if not normalized:
        return None
    for phrases_by_category in _INDIC_PHRASES.values():
        for category, phrases in phrases_by_category.items():
            if normalized in phrases:
                return category
    for patterns in _INDIC_PATTERNS.values():
        for pattern, category in patterns:
            if pattern.match(normalized):
                return category
    stripped = _leading_greeting_stripped(normalized)
    if stripped and stripped != normalized:
        for patterns in _INDIC_PATTERNS.values():
            for pattern, category in patterns:
                if pattern.match(stripped):
                    return category
    return None


def multilingual_reply(category: str, language: LanguageCode) -> str | None:
    """Fixed reply for a category in the requested language.

    Falls back to the English reply when a language has no copy for the
    category — a translated product string, never a legal claim.
    """
    from app.generation.conversation import reply_for_category

    reply = _INDIC_REPLIES.get(language, {}).get(category)
    if reply is not None:
        return reply
    return reply_for_category(category)
