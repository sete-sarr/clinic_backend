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
        # status is read-only here: business/workflow-policy.md restricts Actif/Inactif/Archivé
        # transitions to the dedicated archive/restore actions (validated + audited), never a plain
        # field edit. is_active mirrors status and is derived the same way.
        read_only_fields = ["id", "clinic", "status", "is_active", "created_at", "updated_at"]

    def validate(self, attrs):
        # "Les départements archivés deviennent en lecture seule" (validation-rules.md) — creation
        # is unaffected (self.instance is None then), only edits to an already-archived department.
        if self.instance is not None and self.instance.status == Department.Status.ARCHIVED:
            raise serializers.ValidationError("Archived departments are read-only. Restore it before editing.")
        return attrs
