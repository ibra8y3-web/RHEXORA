# تشغيل فحوص RHEXORA على GitHub

## 1) رفع المشروع

ارفع محتويات مجلد `RHEXORA` إلى مستودع GitHub جديد، مع الاحتفاظ بمجلد `.github/workflows` كما هو. لا ترفع ملفات الدعوات الخاصة أو ملفات `.env` أو `ops/ice/turnserver.conf` أو مفاتيح العقد؛ فهي مستبعدة من Git عبر `.gitignore`.

## 2) الفحوص التي تبدأ تلقائيًا

بعد `push` أو فتح Pull Request، ستظهر هذه الوظائف في تبويب **Actions**:

- **Python tests:** اختبارات Python على Ubuntu وWindows وmacOS، وعلى Python 3.10 إلى 3.13.
- **WebRTC ICE integration:** تثبيت `aiortc` وتشغيل اختبار DataChannel حقيقي يمرر جلسة TLS المثبتة بالبصمة.
- **Android build and unit tests:** بناء Debug APK وRelease APK غير موقّع وتشغيل اختبارات الوحدة.
- **Security analysis:** CodeQL وBandit وpip-audit.
- **Supply-chain and repository security checks:** Gitleaks وTrivy وOpenSSF Scorecard.

افتح كل تشغيل فاشل في Actions، ثم افتح اسم الـJob والخطوة الحمراء وانسخ سجل الخطأ كاملًا عند طلب المساعدة. لا تعتبر وجود Workflow في المستودع دليلًا على نجاحه؛ المطلوب نتيجة خضراء فعلية.

## 3) اختبارات تحتاج بنية تحتية تملكها

### الإنترنت العام وDHT وRelay وICE

انشر أولًا خدمات DHT وRelay وWSS signaling وTURN حسب:

- `docs/GLOBAL_DHT.md`
- `docs/ICE.md`
- `ops_README_RELAY.md`
- `docs/PUBLIC_NETWORK_ACCEPTANCE.md`

بعدها أضف المتغيرات والأسرار المطلوبة في **Settings → Secrets and variables → Actions**. شغّل Workflow **Public network smoke tests** يدويًا. هذا الاختبار صارم: يفشل إذا كانت خدمة مطلوبة غير مضبوطة أو إذا لم يستطع الوصول إلى جار DHT حقيقي أو تنفيذ مهمة عبر ICE/Relay.

ضع المتغير `RHEXORA_PUBLIC_NETWORK_ENABLED=true` فقط بعد اكتمال الإعداد، لتفعيل الفحص الأسبوعي المجدول.

### GPU

يلزم جهاز تملكه أو تتحكم فيه، مع بطاقة مدعومة وتعريف FFmpeg مناسب. سجّل GitHub self-hosted runner بوسوم `self-hosted`, `linux`, `x64`, `rhexora-gpu`، ثم شغّل Workflow **Real GPU video acceptance** يدويًا. السكربت يمنع CPU fallback؛ إذا لم ينجح ترميز على GPU فعلي فستفشل المهمة عمدًا.

### التدقيق الأمني المستقل

فحوص CI ليست تدقيقًا مستقلًا. استخدم `SECURITY_AUDIT_REQUEST.md` لتكليف جهة أمنية مستقلة، ولا تغيّر حالة التدقيق إلى مكتمل قبل استلام تقرير مؤرخ وإعادة اختبار الملاحظات.

## 4) ماذا تعني النتيجة؟

- أخضر في Python tests = نجاح الاختبارات الآلية على بيئات CI المحددة فقط.
- أخضر في ICE = نجاح اختبار WebRTC/TLS على بيئة GitHub Actions، وليس ضمانًا لكل شبكات NAT/CGNAT.
- أخضر في Public network = نجاح الاتصالات مع الخوادم والعقد التي أعددتها وقت الاختبار.
- أخضر في GPU = نجاح العتاد الموجود على ذلك الـRunner فقط.
- APK مبني = الحزمة بُنيت؛ لا يثبت ذلك وحده أنها اختُبرت على كل الهواتف.
- لا يوجد تدقيق مستقل إلا بتقرير طرف ثالث.

الإصدار الحالي `0.4.0a1` ألفا، ولا يُعلن `1.0` قبل إغلاق معايير القبول في `IMPLEMENTATION_STATUS.md` و`docs/ROADMAP.md`.
