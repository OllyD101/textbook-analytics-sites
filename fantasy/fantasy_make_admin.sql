-- Hessle CC Fantasy: make someone a club admin (they'll see the Admin tab after refreshing the page).
-- 1. They must have created an account on the fantasy page first.
-- 2. Put their email address below, then run this in Supabase: SQL Editor -> New query -> Run.
insert into public.fantasy_admins (user_id)
select id from auth.users where lower(email) = lower('your.email@example.com')
on conflict (user_id) do nothing;

-- Check: lists everyone who is an admin
select u.email, a.added_at from public.fantasy_admins a join auth.users u on u.id = a.user_id;

-- To remove an admin:
--   delete from public.fantasy_admins where user_id = (select id from auth.users where lower(email) = lower('their.email@example.com'));
