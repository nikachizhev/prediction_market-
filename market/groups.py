from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import PermissionDenied
from .identity import get_trader
from .models import Group, GroupMembership, Trader
from .notifications import notify


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name", "description", "kind"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


@login_required
def index(request):
    trader = get_trader(request)
    query = request.GET.get("q", "").strip()[:120]
    # Telegram chats (spec E8) live in the Mini App: membership there comes only from Telegram.
    sitegroups = Group.objects.exclude(kind="chat")
    mine = sitegroups.filter(memberships__trader=trader, memberships__status="active")
    found = sitegroups.filter(name__icontains=query).exclude(pk__in=mine.values("pk"))[:30] if query else []
    return render(request, "market/groups.html", {"groups": mine, "found": found, "query": query,
        "pending": GroupMembership.objects.filter(trader=trader).exclude(status="active").exclude(group__kind="chat").select_related("group")})


@login_required
def create(request):
    form = GroupForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            group = form.save(commit=False)
            group.owner = get_trader(request)
            group.save()
            GroupMembership.objects.create(group=group, trader=group.owner, status="active")
        return redirect("market:group_detail", pk=group.pk)
    return render(request, "market/group_form.html", {"form": form})


@login_required
def detail(request, pk):
    trader = get_trader(request)
    group = get_object_or_404(Group.objects.select_related("owner").exclude(kind="chat"), pk=pk)
    is_admin = group.owner_id == trader.pk
    membership = GroupMembership.objects.filter(group=group, trader=trader).first()
    if request.method == "POST":
        action = request.POST.get("action")
        with transaction.atomic():
            # Serialize membership changes and invitation/request races per group.
            group = Group.objects.select_for_update().get(pk=pk)
            membership = GroupMembership.objects.filter(group=group, trader=trader).first()
            if action == "join":
                member, created = GroupMembership.objects.get_or_create(group=group, trader=trader, defaults={"status": "requested"})
                if created:
                    notify(group.owner, f"{trader.display_name} хочет вступить в «{group.name}».", kind="group_request", group=group)
                messages.info(request, "Приглашение уже ожидает вашего решения." if member.status == "invited" else "Заявка отправлена администратору.")
            elif action in ("accept_invite", "decline", "leave"):
                if membership and not is_admin:
                    if action == "accept_invite" and membership.status == "invited":
                        membership.status = "active"
                        membership.save(update_fields=["status"])
                        notify(group.owner, f"{trader.display_name} принял приглашение в «{group.name}».", group=group)
                    elif action in ("decline", "leave"):
                        membership.delete()
                else:
                    messages.error(request, "Владелец остаётся администратором своей группы.")
            elif action in ("invite", "approve", "reject", "remove", "repository", "disconnect_repo", "sync"):
                if not is_admin:
                    raise PermissionDenied
                if action == "invite":
                    target = Trader.objects.filter(user__username__iexact=request.POST.get("username", "").strip(), user__is_active=True).first()
                    if not target or target.pk == trader.pk:
                        messages.error(request, "Участник не найден или вы указали себя.")
                    else:
                        member, created = GroupMembership.objects.get_or_create(group=group, trader=target, defaults={"status": "invited"})
                        if created:
                            notify(target, f"Вас пригласили в группу «{group.name}».", kind="group_invite", group=group)
                            messages.success(request, "Приглашение отправлено.")
                        else:
                            messages.info(request, "Участник уже в группе, приглашён или подал заявку.")
                elif action in ("approve", "reject", "remove"):
                    member = get_object_or_404(GroupMembership.objects.select_for_update(), group=group, pk=request.POST.get("member_id"))
                    if member.trader_id == group.owner_id:
                        raise PermissionDenied
                    if action == "approve" and member.status == "requested":
                        member.status = "active"
                        member.save(update_fields=["status"])
                        notify(member.trader, f"Вы приняты в группу «{group.name}».", kind="group_accepted", group=group)
                    elif action in ("reject", "remove"):
                        member.delete()
                elif action == "repository":
                    from sources.github import parse_repo_url
                    from sources.models import GitHubRepo
                    if group.kind != "work":
                        raise PermissionDenied
                    parsed = parse_repo_url(request.POST.get("repository", ""))
                    if parsed:
                        GitHubRepo.objects.get_or_create(group=group, owner=parsed[0], name=parsed[1])
                        messages.success(request, "Репозиторий подключён. Нажмите «Синхронизировать», чтобы получить вопросы.")
                    else:
                        messages.error(request, "Укажите ссылку вида https://github.com/owner/repo.")
                elif action == "disconnect_repo":
                    from sources.models import GitHubRepo
                    repo = get_object_or_404(GitHubRepo, group=group, pk=request.POST.get("repo_id"))
                    repo.delete()
                    messages.success(request, "Репозиторий отключён. Созданные вопросы сохранены в группе.")
        # Network operations run outside the membership transaction.
        if action == "sync" and is_admin:
            from sources.github import GitHubSource
            from sources.sync import sync_all
            changes = sync_all([GitHubSource(group_id=group.pk)])
            for text in changes:
                messages.info(request, text)
        return redirect("market:group_detail", pk=pk)
    active = bool(membership and membership.status == "active")
    return render(request, "market/group_detail.html", {"group": group, "membership": membership,
        "is_member": active, "is_admin": is_admin,
        "members": group.memberships.filter(status="active").select_related("trader__user") if active else [],
        "requests": group.memberships.exclude(status="active").select_related("trader__user") if is_admin else [],
        "questions": group.questions.all()[:30] if active else [],
        "repos": group.repositories.all() if active and group.kind == "work" else [],
    })
