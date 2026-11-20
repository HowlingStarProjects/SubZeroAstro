"""The SubZeroAstro Licence Agreement (EULA): shown, with I Accept and Quit, the first time SubZeroAstro opens and
again whenever the agreement changes (EULA_VERSION). Help › Licence Agreement shows it any time.

To change the wording: edit SUMMARY / FULL_TEXT here, then raise EULA_VERSION so everyone is asked to accept the new
version. The master copy for the lawyer is a separate document (project files, legal/); keep the two the same.
"""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QTextBrowser,
                               QVBoxLayout)

EULA_VERSION = "1.0-draft"              # raise whenever the wording changes: everyone is asked to accept again
KEY_VERSION = "eula/accepted_version"   # in SubZeroAstro's settings: the version this person accepted, and when
KEY_WHEN = "eula/accepted_at"
TITLE = "SubZeroAstro Licence Agreement"

SUMMARY = [
    '**It\'s a hobby tool, provided "as is."** SubZeroAstro is made for amateur astrophotographers to use for their own personal enjoyment. There is no warranty of any kind, and our responsibility to you is limited.',
    '**Back up your pictures.** SubZeroAstro can move, compress and delete files on your computers, NAS and imaging rigs when you ask it to. Keep your own backups; we are not responsible for lost data.',
    "**You get a licence, not ownership of the app.** Use it on the number of computers your licence allows. Don't copy, share, crack or resell it. Your pictures always belong to you.",
    '**Subscriptions renew until you cancel.** After a trial or subscription ends, you keep every finished picture, but new processing stops until you renew.',
]

FULL_TEXT = """\
## 1. Who this agreement is between

This is an agreement between you and Howling Star Projects, LLC, a Texas company doing business as SubZeroAstro ("we", "us"). It covers the SubZeroAstro app, its updates, its built-in help and manual, and any licence key or sign-in that goes with it (together, "the app").

If you are accepting for someone else, such as a club or a family member, you confirm you are allowed to agree for them. You must be at least 18, or have a parent or guardian accept for you.

## 2. What you are getting

We give you a personal, non-exclusive, non-transferable licence to install and use the app, for as long as your licence is active, under the terms below. You are getting permission to use the app. You are not buying the app or any copyright in it. We keep all rights we don't expressly give you.

Older versions of SubZeroAstro that were released under the MIT open-source licence stay under that licence. This agreement covers versions released with it.

## 3. Made for hobby and personal use

SubZeroAstro is made for amateur astrophotographers to use for their own personal enjoyment and learning. You may share, publish, print, enter in contests and sell your own pictures that you process with it.

The app is **not** designed or tested for professional, scientific, commercial-service, safety-critical or mission-critical use. Do not rely on it for scientific measurements, research results, paid image-processing services for other people, or any purpose where a mistake could cause injury, financial loss or loss of irreplaceable data. If you use it that way, you do so entirely at your own risk.

## 4. Free trial, subscriptions and buy-once licences

- **Free trial.** New users may get a free trial (currently 30 days). A payment card may be required to start it. Unless you cancel before the trial ends, your subscription starts and your card is charged on the day after the trial ends.
- **Subscriptions** (currently $12 per month or $120 per year) renew automatically at the end of each period, at the price then in effect, until you cancel. You can cancel at any time. Cancelling stops the next renewal; you keep access until the end of the period you already paid for.
- **Buy-once licence** (currently about $250) lets you use the version you bought, and the updates included with it [for example: all updates for 12 months], without a subscription.
- **When a trial or subscription ends,** you keep every picture and file you made. The app may still open, view and export your finished work, but new processing and reprocessing will stop until you renew.
- **Payments** are handled by our payment provider. Prices may change; we will give notice before a change affects a renewal. Taxes may apply. Refunds follow the refund policy shown at checkout [policy to be decided].

## 5. Computers and licence checks

Your licence lets you use the app on a set number of computers [number to be set] that you own or control. To keep licences fair, the app checks your licence online from time to time, for example when it starts. If it can't reach our licence server for a while [period to be set], some features may pause until it can check again. You can move your licence to a different computer by signing out of the old one, or by contacting us.

## 6. What you may not do

You may not, and may not help anyone else to:

- copy, share, sell, rent, lend or give away the app, or share your licence key or sign-in with other people;
- get around, disable or tamper with the licence checks or copy protection;
- reverse engineer, decompile or disassemble the app, except where the law allows it even though this agreement says otherwise;
- change the app, or make other software from it;
- remove our name, logo, copyright notices or this agreement;
- use the app to break the law or anyone else's rights.

## 7. Your pictures and data belong to you

Everything you capture, process and create with the app (your images, files, notes and settings) is yours. We don't claim any ownership of it, and we don't need any permission to use it, because it stays on your own computers and storage.

## 8. The app moves and deletes files when you ask it to: back up

Some features work directly on your files, on your own computers, network storage (NAS), imaging computers and devices such as an ASIAIR. These include organizing (moving and renaming), cleaning up imaging computers, deleting exact duplicates, and squeezing (compressing) files. The app asks before deleting, but any software can make mistakes and computers, networks and drives can fail.

**You are responsible for keeping your own backups of anything you can't afford to lose**, and for checking settings before you run a feature. We are not responsible for lost, damaged, moved or deleted files, however it happens.

## 9. Other people's tools and services

The app can work with tools and services made by other people, for example RC-Astro's XTerminator tools, GraXpert, ASTAP, N.I.N.A., ZWO ASIAIR, AstroBin, weather forecast services and online star catalogues. Those are covered by their own licences and terms, which you are responsible for following, and some may need their own purchase. We don't control them and aren't responsible for them, their accuracy or their availability.

## 10. Privacy

We try to collect as little as possible.

- **Your pictures and files** stay on your own computers and storage. The app does not upload them anywhere unless you choose to (for example, to AstroBin).
- **Licence checks** send us your licence key or sign-in, a code that identifies the computer (not its contents), the app version and the date. We use this only to manage your licence.
- **Weather and sky features** send an approximate location (which may be worked out from your internet address) to the weather or catalogue services that provide the forecast or star data.
- **Weather Prayers** you choose to send include the prayer, and the name and place you type. They are reviewed before anything is shown, and approved ones may be shown to other users and on our website.
- **Passwords** you save for your imaging computers stay in your computer's own secure password store. They are never sent to us and never written to logs.
- **Logs** stay on your computer. If you choose to send them to us for help, we use them only to help you.

We don't sell your information. [Link to a fuller privacy policy, if one is published.]

## 11. Updates and changes to this agreement

We may release updates that add, change or remove features. We may also update this agreement. If we do, the app will show you the new version and ask you to accept it before you keep using it. If you don't accept, you can cancel your subscription.

## 12. No warranty

THE APP IS PROVIDED "AS IS" AND "AS AVAILABLE", WITH ALL FAULTS AND WITHOUT ANY WARRANTY OF ANY KIND, EXPRESS OR IMPLIED. TO THE FULLEST EXTENT THE LAW ALLOWS, WE DISCLAIM ALL WARRANTIES, INCLUDING ANY IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, ACCURACY AND NON-INFRINGEMENT. We don't promise that the app will be error-free, will run without interruption, will work with your equipment, or will produce any particular result.

## 13. Limits on our responsibility

TO THE FULLEST EXTENT THE LAW ALLOWS:

- WE ARE NOT LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL OR PUNITIVE DAMAGES, OR FOR ANY LOSS OF DATA, IMAGES, IMAGING TIME, EQUIPMENT, PROFITS OR GOODWILL, EVEN IF WE WERE TOLD IT COULD HAPPEN.
- OUR TOTAL LIABILITY FOR ANYTHING RELATED TO THE APP OR THIS AGREEMENT IS LIMITED TO THE GREATER OF THE AMOUNT YOU PAID US FOR THE APP IN THE 12 MONTHS BEFORE THE CLAIM, OR US$50.

Some places don't allow some of these limits, so they may not all apply to you. You agree to cover us for claims and costs caused by your breaking this agreement or the law while using the app.

## 14. Ending this agreement

You can end this agreement at any time by cancelling any subscription and uninstalling the app. We can end or suspend your licence if you break this agreement, for example by sharing your key or getting around the licence checks. When it ends, you must stop using the app, but your pictures and files remain yours. Sections 7, 8, 9 and 12 to 16 continue after it ends.

## 15. Law and disputes

Texas law governs this agreement, without regard to its conflict-of-law rules. Any dispute will be handled in the state or federal courts in [County], Texas, unless the law where you live gives you the right to bring it there. Before going to court, please contact us so we can try to sort it out. [Arbitration or small-claims clause: lawyer to advise.]

## 16. General

- **Whole agreement.** This is the whole agreement between us about the app, and it replaces anything said before.
- **If part is invalid,** the rest still applies.
- **Not giving up rights.** If we don't enforce something right away, we can still enforce it later.
- **Transfer.** You can't transfer this agreement or your licence without our written consent. We may transfer it if the business changes hands.
- **Export.** You agree to follow export and sanctions laws that apply to you.

## 17. Contact

Howling Star Projects, LLC (SubZeroAstro), Texas · [support email] · subzeroastro.com
"""


def is_draft() -> bool:
    """Until the lawyer has approved it, the agreement says it is a draft."""
    return EULA_VERSION.endswith("draft")


def needs_acceptance(settings) -> bool:
    """True when this person hasn't accepted the current version yet (a new install, or the agreement changed)."""
    return str(settings.value(KEY_VERSION, "") or "") != EULA_VERSION


def record_acceptance(settings) -> None:
    settings.setValue(KEY_VERSION, EULA_VERSION)
    settings.setValue(KEY_WHEN, datetime.now().isoformat(timespec="seconds"))
    settings.sync()


def summary_html() -> str:
    import re

    def md(s):
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    items = "".join(f"<li style='margin-bottom:6px'>{md(b)}</li>" for b in SUMMARY)
    return f"<ul style='margin-left:-20px'>{items}</ul>"


def document_markdown(with_summary=True) -> str:
    """The whole agreement as one page (summary first), for the text box."""
    head = f"# {TITLE}\n\nVersion {EULA_VERSION} · Howling Star Projects, LLC (SubZeroAstro), Texas\n\n"
    if is_draft():
        head += "*Draft: this agreement is being reviewed by our lawyer and may still change. If it does, " \
                "SubZeroAstro will show you the new version.*\n\n"
    if with_summary:
        head += "## The short version\n\n" + "".join(f"- {b}\n" for b in SUMMARY) + "\n## The full agreement\n\n"
    return head + FULL_TEXT


class EulaDialog(QDialog):
    """Ask mode (first run / changed agreement): I Accept + Quit, Accept only after ticking 'I have read…'.
    Show mode (Help › Licence Agreement): the same text with a Close button."""

    def __init__(self, parent=None, ask=True, changed=False):
        super().__init__(parent)
        self.ask = ask
        self.setWindowTitle(TITLE)
        self.setMinimumSize(560, 460)
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        if ask:
            intro = ("<h2 style='margin-bottom:2px'>Before you start</h2>"
                     + ("The SubZeroAstro Licence Agreement has changed. Please read the new version and press "
                        "<b>I Accept</b> to carry on." if changed else
                        "Please read the SubZeroAstro Licence Agreement. The short version:"))
            top = QLabel(intro + summary_html())
            top.setWordWrap(True)
            top.setTextFormat(Qt.RichText)
            lay.addWidget(top)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.setMarkdown(document_markdown(with_summary=not ask))
        lay.addWidget(self.text, 1)
        self.buttons = QDialogButtonBox()
        if ask:
            self.chk = QCheckBox("I have read the agreement and I accept it")
            lay.addWidget(self.chk)
            self.btn_accept = QPushButton("I Accept")
            self.btn_accept.setDefault(True)
            self.btn_accept.setEnabled(False)
            self.btn_quit = QPushButton("Quit")
            self.btn_quit.setToolTip("Close SubZeroAstro without accepting")
            self.chk.toggled.connect(self.btn_accept.setEnabled)
            self.buttons.addButton(self.btn_accept, QDialogButtonBox.AcceptRole)
            self.buttons.addButton(self.btn_quit, QDialogButtonBox.RejectRole)
        else:
            self.buttons.addButton(QDialogButtonBox.Close)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        if ask:
            note = QLabel(f"<small>Agreement version {EULA_VERSION}. You can read it again any time under "
                          "<i>Help › Licence Agreement</i>.</small>")
            note.setWordWrap(True)
            lay.addWidget(note)


def ask_if_needed(settings, parent=None) -> bool:
    """Shown before the main window. True: accepted (now or before); False: the person pressed Quit."""
    if not needs_acceptance(settings):
        return True
    changed = bool(str(settings.value(KEY_VERSION, "") or ""))
    dlg = EulaDialog(parent, ask=True, changed=changed)
    if dlg.exec() == QDialog.Accepted:
        record_acceptance(settings)
        return True
    return False


def show(parent=None) -> None:
    """Help › Licence Agreement."""
    EulaDialog(parent, ask=False).exec()
