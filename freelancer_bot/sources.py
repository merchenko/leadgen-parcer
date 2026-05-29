from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    handle: str
    title: str
    reason: str
    enabled: bool = True

    @property
    def username(self) -> str:
        return self.handle.removeprefix("@")

    @property
    def telegram_url(self) -> str:
        return f"https://t.me/{self.username}"


SOURCES: list[Source] = [
    Source("@freelansim_ru", "Хабр Фриланс", "часто публикует подборки в категории боты и парсинг"),
    Source("@apibot_tg", "BotAPI [Фриланс]", "узко про заказы на Telegram-ботов", enabled=False),
    Source("@digitaltender", "DIGITAL Tender", "крупный канал с digital-заказами и разработкой"),
    Source("@freelancetaverna", "Фриланс Таверна", "много удаленных задач и вакансий для разработчиков"),
    Source("@frilans", "Фриланс | Удаленная работа", "широкий фриланс-канал, фильтр режет нерелевантное"),
    Source("@job_developer", "Фриланс для разработчиков", "разработка, удаленка, фриланс-заказы"),
    Source("@search_techspec", "Ищу Техспец", "заказы для технических специалистов и разработчиков"),
    Source("@search_zakaz", "Ищу Заказы", "агрегатор заказов, полезен для расширения выдачи"),
    Source("@FreeVacanciesIT", "IT Фриланс | Вакансии", "IT-разовая и проектная работа"),
    Source("@freelance_dev_work", "Kwork разработка и IT", "агрегатор Kwork-заказов по разработке"),
    Source("@do_it_order", "Do IT - фриланс заказы", "агрегирует программирование и разработку", enabled=False),
    Source("@pixeltechspec", "Pixel | Заказы для Тех-спецов", "проектные заказы для техспецов"),
    Source("@webfrl", "Web Freelance", "web/fullstack задачи, иногда боты и API"),
    Source("@workayte", "Работа в ИТ", "IT-вакансии и удаленные проектные задачи"),
    Source("@FreelancehuntProjects", "Freelancehunt Projects", "лента проектов с биржи Freelancehunt"),
]


def enabled_sources() -> list[Source]:
    return [source for source in SOURCES if source.enabled]

