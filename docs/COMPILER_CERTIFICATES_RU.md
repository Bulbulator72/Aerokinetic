# InvalidCertificate при автономной установке Build Tools

## Что подтверждено журналом пользователя

На фото `dd_bootstrapper_20261027110915.log` указаны Windows NT 10.0.19045,
bootstrapper Build Tools 17.14.37824.6 и следующие строки:

```text
NoWeb or OfflineFilePath specified, skipping latest installer feed check.
Using Offline package: ...\downloads\vs_buildtools\vs_installer.opc
Certificate is invalid: ...\downloads\vs_buildtools\vs_installer.opc
Error: Unable to verify the certificate: InvalidCertificate
Error 0x80131509: Signature verification failed.
```

Это конкретизирует ранее полученный код 5003: локальный пакет Installer
обнаружен, но его сертификат не проходит проверку. Снимок не содержит кода
`0x800B0109` и не называет конкретный недоверенный корень. Первоначальная
реплика ассистента с этим кодом была ошибкой чтения фотографии и исправлена.
Отсутствие/неактуальность сертификатов Microsoft — проверяемая причина,
для которой Microsoft документирует самостоятельное автономное исправление.
Полнота всего layout и отсутствие повреждений пакета ещё не установлены.
После импорта корневых сертификатов пользователь снова получил 5003;
подробности нового журнала ещё не получены. Для повторной подготовки дома
добавлен [порядок проверки и актуальный промежуточный сертификат](HOME_COMPILER_PREP_RU.md).

## Действия на рабочем ПК без интернета

Использовать папку `downloads\vs_buildtools\Certificates` из того же
официального комплекта Microsoft, который был подготовлен дома.

По документации Microsoft в ней находятся:

| Файл | Корневой сертификат |
|---|---|
| `manifestRootCertificate.cer` | Microsoft Root Certificate Authority 2011 |
| `manifestCounterSignRootCertificate.cer` | Microsoft Root Certificate Authority 2010 |
| `vs_installer_opc.RootCertificate.cer` | Microsoft Root Certificate Authority 2010 |

Для каждого из этих файлов:

1. Правая кнопка → **«Установить сертификат»**.
2. Выбрать **«Локальный компьютер»**; подтвердить стандартный запрос прав.
3. **«Поместить все сертификаты в следующее хранилище»** → **«Обзор»** →
   **«Доверенные корневые центры сертификации»**.
4. Завершить мастер и убедиться, что импорт успешен.

Затем закрыть прежнее окно установщика и повторить
`2_install_COMPILER_OFFLINE.bat` от администратора. После успеха и требуемой
перезагрузки выполнить BAT 3 с численными проверками и BAT 6 для запуска.

Если папки/файлов нет, требуется уточнить состав перенесённого layout,
не импортировать случайные сертификаты. Если ошибка сохраняется после
успешного импорта, нужен свежий bootstrapper-журнал: сертификаты могут
быть не единственной причиной. В частности, новая цепочка подписи может
потребовать дополнительного промежуточного сертификата, а пакет может
нуждаться в проверке целостности по исходному layout.

## Документированный командный вариант

Microsoft приводит следующие команды для повышенного процесса. В BAT
в папке программы `[layout path]` соответствует
`%~dp0downloads\vs_buildtools`. Команды добавляют только три явно названных
файла из комплекта в хранилище Root локального компьютера:

```bat
certutil.exe -addstore -f "Root" "%~dp0downloads\vs_buildtools\Certificates\manifestRootCertificate.cer"
certutil.exe -addstore -f "Root" "%~dp0downloads\vs_buildtools\Certificates\manifestCounterSignRootCertificate.cer"
certutil.exe -addstore -f "Root" "%~dp0downloads\vs_buildtools\Certificates\vs_installer_opc.RootCertificate.cer"
```

Шаблон `%~dp0` применим внутри BAT. Это документация команд, а не
автоматически исполняемый импорт. Изменение доверенных сертификатов
локального компьютера является частью указанной процедуры Microsoft;
проверка подписей остаётся включённой. Текущее изменение проекта само
сертификаты не устанавливает.

## Источник и границы проверки

Официальная инструкция Microsoft, разделы Option 1, Option 3 и описание
трёх файлов сертификатов, прочитана через Firecrawl 27 октября 2026 г.:

https://learn.microsoft.com/en-us/visualstudio/install/install-certificates-for-visual-studio-offline?view=vs-2022

Проверены чтение фото и соответствие имён, корней и хранилища инструкции.
В этой Linux-среде сертификаты Windows не импортировались и MSVC не
запускался: `not_run`. Устранение ошибки на ПК пользователя ещё не
подтверждено. Расчётный код и его численные настройки не менялись.
