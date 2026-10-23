"""
Translations for the handful of API messages a *shopper* can see.

The admin panel is a staff tool and its API errors stay English; these are the
storefront ones, which surface directly in the checkout and order-lookup UI. The
active language comes from the `Accept-Language` header the storefront sends
(see storefront/src/lib/api.ts).

Keys are English source strings so a missing translation degrades to readable
English rather than to a key name.
"""
from typing import Optional

SUPPORTED_LOCALES = ("en", "fa", "ar")
DEFAULT_LOCALE = "en"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "fa": {
        "Product not found": "محصول یافت نشد",
        "Variant not found": "این پیکربندی یافت نشد",
        "Some items are no longer available and were removed from sale. "
        "Remove them from your cart and try again.": "برخی اقلام دیگر موجود نیستند و از فروش خارج شده‌اند. "
        "آن‌ها را از سبد خرید حذف کنید و دوباره تلاش کنید.",
        "Some items in your cart are no longer on sale. Remove them and try again.":
            "برخی اقلام سبد خرید شما دیگر برای فروش نیستند. آن‌ها را حذف کنید و دوباره تلاش کنید.",
        "Not enough stock for: ": "موجودی کافی نیست برای: ",
        "{left} left, {requested} requested": "{left} عدد موجود، {requested} عدد درخواست شده",
        "We couldn't place the order. Nothing was charged — please try again.":
            "ثبت سفارش انجام نشد. مبلغی دریافت نشده — لطفاً دوباره تلاش کنید.",
        "No order with that reference. Check the code from your confirmation email.":
            "سفارشی با این کد پیگیری یافت نشد. کد را از ایمیل تأیید سفارش بررسی کنید.",
        "You've sent several messages already. Give us a little time to reply "
        "before sending another.": "شما چند پیام فرستاده‌اید. کمی به ما فرصت دهید تا پاسخ دهیم، "
        "سپس دوباره پیام بفرستید.",
        "Thanks — your message reached our team. We reply to enquiries within one "
        "working day.": "سپاسگزاریم — پیام شما به تیم ما رسید. معمولاً ظرف یک روز کاری پاسخ می‌دهیم.",
        "Search is temporarily unavailable. Please try again in a moment.":
            "جست‌وجو موقتاً در دسترس نیست. لطفاً چند لحظه بعد دوباره تلاش کنید.",
    },
    "ar": {
        "Product not found": "المنتج غير موجود",
        "Variant not found": "هذه التهيئة غير موجودة",
        "Some items are no longer available and were removed from sale. "
        "Remove them from your cart and try again.": "بعض العناصر لم تعد متاحة وأُزيلت من البيع. "
        "احذفها من سلتك وحاول مرة أخرى.",
        "Some items in your cart are no longer on sale. Remove them and try again.":
            "بعض العناصر في سلتك لم تعد معروضة للبيع. احذفها وحاول مرة أخرى.",
        "Not enough stock for: ": "المخزون غير كافٍ لـ: ",
        "{left} left, {requested} requested": "المتبقي {left}، المطلوب {requested}",
        "We couldn't place the order. Nothing was charged — please try again.":
            "تعذّر تسجيل الطلب. لم يُخصم أي مبلغ — حاول مرة أخرى.",
        "No order with that reference. Check the code from your confirmation email.":
            "لا يوجد طلب بهذا الرقم المرجعي. تحقق من الرمز في بريد تأكيد الطلب.",
        "You've sent several messages already. Give us a little time to reply "
        "before sending another.": "لقد أرسلت عدة رسائل بالفعل. امنحنا بعض الوقت للرد قبل إرسال رسالة أخرى.",
        "Thanks — your message reached our team. We reply to enquiries within one "
        "working day.": "شكرًا لك — وصلت رسالتك إلى فريقنا. نرد على الاستفسارات خلال يوم عمل واحد.",
        "Search is temporarily unavailable. Please try again in a moment.":
            "البحث غير متاح مؤقتًا. يُرجى المحاولة مرة أخرى بعد قليل.",
    },
}


def resolve_locale(accept_language: Optional[str]) -> str:
    """
    Minimal Accept-Language parse: the storefront sends a bare tag ("fa"), but a
    browser may send a weighted list, so take the first supported primary subtag.
    """
    if not accept_language:
        return DEFAULT_LOCALE
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip().lower()
        primary = tag.split("-")[0]
        if primary in SUPPORTED_LOCALES:
            return primary
    return DEFAULT_LOCALE


def translate(message: str, locale: str) -> str:
    return TRANSLATIONS.get(locale, {}).get(message, message)
