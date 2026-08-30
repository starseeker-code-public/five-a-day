import logging

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import CreateView

from students.forms import ParentForm
from students.models import Parent

logger = logging.getLogger(__name__)


class ParentCreateView(CreateView):
    model = Parent
    form_class = ParentForm
    template_name = "parent_create.html"

    def get_waiting_entry(self):
        """Waiting-list entry this enrollment came from (`?from_waiting=<id>`), if any."""
        from core.views.waiting_list import waiting_entry_from_request

        return waiting_entry_from_request(self.request)

    def get_initial(self):
        """Prefill the contact taken over the phone when coming from the waiting list."""
        initial = super().get_initial()
        waiting = self.get_waiting_entry()
        if waiting:
            contact = (waiting.waiting_contact_name or "").strip()
            if contact:
                first, _, last = contact.partition(" ")
                initial["first_name"] = first
                initial["last_name"] = last.strip()
            initial["phone"] = waiting.waiting_contact_phone or ""
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["waiting_entry"] = self.get_waiting_entry()
        return context

    def get_success_url(self):
        url = str(reverse_lazy("student_create")) + f"?parent_id={self.object.id}"
        waiting = self.get_waiting_entry()
        if waiting:
            url += f"&from_waiting={waiting.id}"
        return url

    def form_valid(self, form):
        try:
            dni = form.cleaned_data.get("dni")
            existing_parent = Parent.objects.filter(dni=dni).first()

            if existing_parent:
                messages.info(
                    self.request,
                    f"El padre/tutor {existing_parent.full_name} ya existe. Serás redirigido para crear un estudiante.",
                )
                self.object = existing_parent
                return HttpResponseRedirect(self.get_success_url())

            self.object = form.save()
            messages.success(
                self.request,
                f"Padre/tutor {self.object.full_name} creado exitosamente. Ahora crea un estudiante para este padre.",
            )
            return HttpResponseRedirect(self.get_success_url())

        except Exception:
            # Never echo str(e) — an IntegrityError leaks the table and column.
            logger.exception("Error creating parent")
            messages.error(self.request, "Error al crear el padre/tutor. Revisa los datos e inténtalo de nuevo.")
            return self.form_invalid(form)
