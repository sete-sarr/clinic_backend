from rest_framework import serializers

from departments.models import Department


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = [
            "id",
            "clinic",
            "name",
            "code",
            "department_type",
            "description",
            "status",
            "is_active",
            "created_at",
            "updated_at",
        ]
        # status est en lecture seule ici : business/workflow-policy.md restreint les transitions
        # Actif/Inactif/Archivé aux actions dédiées archive/restore (validées + auditées), jamais à
        # une simple modification de champ. is_active reflète status et est dérivé de la même façon.
        read_only_fields = ["id", "clinic", "status", "is_active", "created_at", "updated_at"]

    def validate(self, attrs):
        # "Les départements archivés deviennent en lecture seule" (validation-rules.md) — la
        # création n'est pas affectée (self.instance vaut alors None), seules les modifications
        # d'un département déjà archivé le sont.
        if self.instance is not None and self.instance.status == Department.Status.ARCHIVED:
            raise serializers.ValidationError("Archived departments are read-only. Restore it before editing.")
        return attrs
