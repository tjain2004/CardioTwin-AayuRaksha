import { useState } from 'react'
import { Card, Icon } from '../components/ui'

// CAD Education Center: short, plain-language pages on coronary artery disease. General education from public
// guidance (WHO, AHA/ACC, ESC, ICMR-NIN, ADA, Lipid Association of India); not personal medical advice.
const TOPICS = [
  { id: 'what', title: 'What is coronary artery disease?', body: [
    'Coronary arteries are the small blood vessels on the surface of the heart that bring it oxygen.',
    'In coronary artery disease (CAD), these arteries become narrowed by a fatty build-up called plaque. Less blood reaches the heart muscle, especially when it works harder.',
    'CAD can cause chest discomfort on effort (angina), breathlessness, or a heart attack if a plaque breaks open and a clot blocks the artery.',
    'It is common in India and often starts at a younger age than in many other countries. It can be prevented and treated.',
  ] },
  { id: 'risk', title: 'Risk factors', body: [
    'Ones you can change: smoking or chewing tobacco, high blood pressure, high LDL cholesterol, diabetes, excess weight (for Indian adults a BMI of 23 or more, or a waist above 90 cm for men and 80 cm for women), little physical activity, an unhealthy diet, stress and poor sleep.',
    'Ones you cannot change: older age, being male, a parent or sibling with early heart disease (a man before 55 or a woman before 65), and South Asian ancestry.',
    'Risk factors add up. Improving even one or two lowers your overall risk.',
  ] },
  { id: 'plaque', title: 'How plaque develops', body: [
    'It starts with damage to the inner lining of the artery, from high blood pressure, smoking, high sugar or high LDL cholesterol.',
    'LDL cholesterol slips into the damaged wall. The body sends inflammatory cells, and over years a plaque of fat, cells and calcium grows. This is called atherosclerosis.',
    'A stable plaque can slowly narrow the artery. An unstable plaque can crack, a clot forms on it, and the artery can block suddenly: a heart attack.',
  ] },
  { id: 'chol', title: 'Cholesterol', body: [
    'LDL ("bad") cholesterol builds plaque. HDL ("good") cholesterol helps carry it away. Triglycerides are another blood fat that rises with sugar, refined carbohydrates and alcohol.',
    'A lipid profile blood test measures these. Your doctor sets your target based on your overall risk; people at higher risk need lower LDL.',
    'Helpful habits: less ghee, butter, vanaspati and fried snacks; more vegetables, dal, whole grains, nuts and seeds; regular activity. Many people also need a statin, which only a doctor can prescribe.',
  ] },
  { id: 'bp', title: 'Blood pressure', body: [
    'High blood pressure strains artery walls and the heart. It usually causes no symptoms, so it must be measured.',
    'A reading of 130/80 mmHg or above is considered raised (ACC/AHA). Your doctor will tell you your own target.',
    'Less salt (under 5 g, about one teaspoon, a day), regular activity, a healthy weight, less alcohol and taking prescribed medicines every day all help.',
  ] },
  { id: 'diab', title: 'Diabetes', body: [
    'Diabetes roughly doubles the risk of heart disease because high sugar damages blood vessels.',
    'HbA1c shows average blood sugar over about three months: 5.7 to 6.4% is prediabetes and 6.5% or more is diabetes (ADA).',
    'Regular checks, a balanced plate, activity, and controlling blood pressure and cholesterol together protect the heart.',
  ] },
  { id: 'smoke', title: 'Smoking and tobacco', body: [
    'Cigarettes, bidis, hookah and chewing tobacco all damage arteries and make clots more likely.',
    'The benefit of stopping starts within weeks; after a year, heart attack risk falls a lot.',
    'Free help: the national quitline 1800-11-2356. Ask your doctor about medicines that make quitting easier.',
  ] },
  { id: 'move', title: 'Physical activity', body: [
    'Adults are advised 150 to 300 minutes a week of moderate activity such as brisk walking (WHO).',
    'With a heart condition, start low and build up slowly, and agree your level with your doctor or a cardiac rehabilitation team.',
    'Use the talk test: you should be able to talk in full sentences. Stop and rest if you get chest pain, unusual breathlessness, dizziness or palpitations.',
    'Sitting less matters too: get up and move for a few minutes every hour.',
  ] },
  { id: 'food', title: 'Nutrition', body: [
    'A heart-healthy plate is about half vegetables and fruit, a quarter whole grains or millets, and a quarter protein such as dal, legumes, curd, eggs, fish or lean meat (ICMR-NIN 2024).',
    'Cook with a little oil, and limit fried food, sweets, sugary drinks, pickles, papad, namkeen and processed meat.',
    'Both vegetarian and non-vegetarian diets can be heart-healthy. See Wellness for daily ideas matched to you.',
  ] },
  { id: 'warn', title: 'Warning signs of a heart attack', body: [
    'Chest pain, pressure, squeezing or heaviness lasting more than a few minutes, or coming and going.',
    'Pain spreading to the arm, jaw, neck, back or upper stomach; breathlessness; cold sweat; nausea; light-headedness or fainting.',
    'Women, older people and people with diabetes may have milder or unusual symptoms such as tiredness or breathlessness only.',
    'Stroke signs (FAST): Face drooping, Arm weakness, Speech difficulty: Time to call 112.',
  ], urgent: true },
  { id: 'prevent', title: 'Prevention', body: [
    'Know your numbers: blood pressure, cholesterol, sugar (HbA1c) and weight.',
    'Do not use tobacco, move every day, eat more plants and less salt, sleep 7 to 9 hours, and manage stress.',
    'Take prescribed medicines regularly and keep follow-up visits. Do not stop a medicine without talking to your doctor.',
  ] },
  { id: 'help', title: 'When to seek help', body: [
    'Call 112 now for chest pain or pressure, severe breathlessness, fainting, or stroke signs. Do not drive yourself.',
    'See your doctor soon for new breathlessness on effort, chest discomfort that comes with activity and settles with rest, swollen ankles, or palpitations.',
    'A CardioTwin result or a watch reading is never a reason to wait if you feel unwell.',
  ], urgent: true },
  { id: 'tests', title: 'Common heart tests', body: [
    'ECG: a quick recording of the heart’s electrical activity.',
    'Blood tests: lipid profile, HbA1c, kidney function, and sometimes troponin when a heart attack is suspected.',
    'Echocardiogram: an ultrasound showing how the heart pumps. Treadmill test (TMT): an ECG while walking on a treadmill.',
    'CT coronary angiography or calcium score: a scan of the coronary arteries. Coronary angiography: a dye test through a thin tube, which can be followed by angioplasty and a stent.',
    'Which tests you need depends on your symptoms and risk; only a doctor can decide.',
  ] },
  { id: 'ask', title: 'Questions to ask your doctor', body: [
    'What is my overall risk of heart disease, and which of my risk factors matter most?',
    'What should my blood pressure, LDL cholesterol and HbA1c targets be?',
    'Do I need any heart tests? What would they show?',
    'What is each of my medicines for, and what side effects should I watch for?',
    'How much and what kind of activity is safe for me?',
    'Which symptoms mean I should call 112 straight away?',
  ] },
]

export default function Learn() {
  const [open, setOpen] = useState(null)
  return (
    <Card title="Heart disease: learn the basics" eyebrow="CAD Education Center">
      <div className="divide-y divide-line">
        {TOPICS.map((t, i) => {
          const on = open === t.id
          return (
            <div key={t.id}>
              <button className="flex w-full items-center gap-3 py-3 text-left" aria-expanded={on} onClick={() => setOpen(on ? null : t.id)}>
                <span className="num flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-bold" style={{ background: t.urgent ? 'var(--emg)' : 'var(--accent-soft)', color: t.urgent ? 'var(--on-emg)' : 'var(--accent)' }}>{i + 1}</span>
                <b className="min-w-0 flex-1 text-[14.5px]">{t.title}</b>
                <Icon name="chevron" size={16} className="text-muted transition-transform" style={{ transform: on ? 'rotate(90deg)' : undefined }} />
              </button>
              {on && (
                <div className="space-y-2 pb-3 pl-10 text-[14px] leading-relaxed">
                  {t.body.map((p, n) => <p key={n}>{p}</p>)}
                  {t.urgent && <a href="tel:112" className="btn btn-emg min-h-11">Call 112</a>}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <p className="mt-2 text-[11.5px] text-muted">General education, not a diagnosis or personal advice. Sources: WHO, AHA/ACC, ESC, ICMR-NIN, ADA, Lipid Association of India.</p>
    </Card>
  )
}
