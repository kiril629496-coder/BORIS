from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
import os
OUT='outreach_assets/Пример_аудита_нерудные_материалы_BORIS.pdf'
font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'; bold='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
pdfmetrics.registerFont(TTFont('DV',font)); pdfmetrics.registerFont(TTFont('DVB',bold))
W,H=A4; c=canvas.Canvas(OUT,pagesize=A4)
BLUE=HexColor('#2563EB'); NAVY=HexColor('#14213D'); GRAY=HexColor('#667085'); PALE=HexColor('#F4F7FF'); GREEN=HexColor('#0E9F6E')
def txt(x,y,s,size=10,font='DV',color=NAVY): c.setFont(font,size); c.setFillColor(color); c.drawString(x,y,s)
def wrap(x,y,s,width=165*mm,size=10,leading=15,font='DV',color=NAVY):
    words=s.split(); line=''; yy=y
    for w in words:
        trial=(line+' '+w).strip()
        if pdfmetrics.stringWidth(trial,font,size)>width:
            txt(x,yy,line,size,font,color); yy-=leading
            line=w
        else: line=trial
    if line: txt(x,yy,line,size,font,color); yy-=leading
    return yy
def header(n,title):
    c.setFillColor(BLUE); c.roundRect(18*mm,H-31*mm,13*mm,13*mm,4*mm,fill=1,stroke=0); txt(21.5*mm,H-24.3*mm,f'{n:02d}',9,'DVB',HexColor('#FFFFFF'))
    txt(36*mm,H-21*mm,'BORIS AI',9,'DVB',BLUE); txt(36*mm,H-28*mm,title,18,'DVB',NAVY)
def footer(): txt(18*mm,12*mm,'Краткий пример аудита · не является аудитом конкретного получателя',8,'DV',GRAY); c.showPage()
# 1
header(1,'Нерудные материалы: пример аудита')
y=H-48*mm; y=wrap(18*mm,y,'Цифровой отдел продаж нового поколения для компаний, которые поставляют песок, щебень, вторичные материалы и организуют вывоз грунта.',165*mm,13,19,'DVB')
c.setFillColor(PALE); c.roundRect(18*mm,y-42*mm,174*mm,34*mm,5*mm,fill=1,stroke=0)
wrap(25*mm,y-17*mm,'Главная идея: спрос в нише есть постоянно. Выигрывает тот, кто быстрее обрабатывает обращение, правильно считает доставку и системно собирает спрос по разным направлениям.',160*mm,11,16,'DV')
y-=58*mm
for title,body in [('Частный клиент','Покупает не фракцию, а решение задачи: въезд, участок, фундамент, парковка, дренаж, вывоз земли.'),('B2B','Прорабу и снабженцу важны объём, график, документы, НДС/без НДС и регулярность поставок.')]:
 txt(18*mm,y,title,12,'DVB',BLUE); y=wrap(18*mm,y-7*mm,body,165*mm,10,15); y-=7*mm
footer()
#2
header(2,'Семь самостоятельных направлений спроса')
y=H-50*mm
items=[('1. Песок','карьерный, намывной, сеяный; фундамент, бетон, участок, плитка, засыпка'),('2. Щебень','гранитный, известняковый, гравийный; 5–20, 20–40, 40–70, ЩПС, отсев'),('3. Вторичный щебень','дороги, подъезды, парковки, площадки, черновое благоустройство'),('4. Бетонный и кирпичный бой','вывоз → переработка и перевозка → повторная продажа'),('5. Вывоз грунта','отдельный продукт со своей географией, объявлениями и ценой обращения'),('6. Комплексные работы','привезли материал → обратным рейсом забрали грунт'),('7. Регулярные поставки','подрядчики, дорожники, благоустройство, производства, УК, СНТ')]
for a,b in items:
 txt(18*mm,y,a,11,'DVB',BLUE); y=wrap(18*mm,y-6*mm,b,165*mm,9.5,13); y-=5*mm
footer()
#3
header(3,'Где обычно теряются деньги')
y=H-49*mm
for a,b in [('Деньги на рекламу вслепую','Не видно цепочки: объявление → расход → обращение → диалог → результат.'),('Медленный ответ','Покупатель часто пишет сразу нескольким поставщикам. Кто быстрее дал понятный расчёт — тот ближе к заказу.'),('Одинаковые объявления','«Низкие цены, быстрая доставка, напрямую с карьера» — это уже пишут почти все.'),('Неправильный диагноз','Много просмотров без обращений и много обращений без продаж — разные проблемы, их нельзя лечить одним увеличением бюджета.')]:
 c.setFillColor(PALE); c.roundRect(18*mm,y-28*mm,174*mm,23*mm,4*mm,fill=1,stroke=0); txt(24*mm,y-12*mm,a,10.5,'DVB',NAVY); wrap(24*mm,y-19*mm,b,158*mm,8.7,12); y-=31*mm
footer()
#4
header(4,'Что меняет BORIS')
y=H-50*mm
for a,b in [('Продажа решения','Не «Щебень 20–40», а «Нужно отсыпать въезд? Подберём материал и рассчитаем объём».'),('Расчёт доставки до заказа','Адрес + объём → понятный итог «материал + доставка».'),('AI-МОП','Контролирует новые сообщения, определяет потребность, собирает адрес и объём, не забывает диалог.'),('AI-РОП','Смотрит на весь отдел: скорость ответа, потерянные обращения, качество переписки, причины отказов и KPI.'),('Один экран','Сообщения, история клиента, очередь и ответственный доступны без переключения между десятками вкладок.')]:
 txt(18*mm,y,a,11,'DVB',GREEN); y=wrap(18*mm,y-6*mm,b,165*mm,9.5,14); y-=8*mm
footer()
#5
header(5,'Как выглядит работа в первый месяц')
y=H-50*mm
steps=['Аудит структуры аккаунта и объявлений','Стратегия по отдельным направлениям спроса','Проработка географии','AI-МОП + AI-РОП','Единый центр сообщений','Контроль звонков и переписок','Реактивация старой базы','Аналитика объявлений и KPI','Оптимизация и сопровождение 30 дней']
for i,s in enumerate(steps,1):
 c.setFillColor(BLUE); c.circle(22*mm,y+2*mm,3*mm,fill=1,stroke=0); txt(20.3*mm,y,f'{i}',7,'DVB',HexColor('#FFFFFF')); txt(29*mm,y,s,10,'DV',NAVY); y-=14*mm
footer()
#6
header(6,'Следующий шаг')
y=H-55*mm; y=wrap(18*mm,y,'Если тема актуальна, BORIS может подготовить уже не общий пример, а короткий разбор именно вашей компании: сайта, рекламы, обработки обращений и точек роста.',165*mm,13,19,'DVB')
y-=10*mm
c.setFillColor(PALE); c.roundRect(18*mm,y-38*mm,174*mm,32*mm,5*mm,fill=1,stroke=0)
wrap(25*mm,y-18*mm,'Для старта достаточно ссылки на сайт и текущих каналов продаж. В ответ можно получить 3–5 конкретных точек роста без обязательств.',155*mm,11,16)
y-=58*mm; txt(18*mm,y,'BORIS AI',17,'DVB',BLUE); txt(18*mm,y-10*mm,'boris-ai.pro',11,'DVB',NAVY); txt(18*mm,y-20*mm,'Цифровой отдел продаж нового поколения',10,'DV',GRAY)
footer(); c.save(); print(OUT,os.path.getsize(OUT))
