select * from participants;

-- num self games and reference games
select 
	case
		when riot_id_game_name = 'xxdragonfruit' then 'self'
		else 'reference'
	end as cohort,
	count(*)
from participants
where champion_name ='Kayle'
group by cohort
;

-- num of games per puuid and per cohort
with player_game_counts as(	
	select 
		case
			when riot_id_game_name = 'xxdragonfruit' then 'self'
			else 'reference'
		end as cohort,
		riot_id_game_name,
		count(*) as num_games
	from participants
	where champion_name ='Kayle'
	group by riot_id_game_name
)
select *,
	sum(num_games) over(
		partition by cohort
	) as cohort_num_games
from player_game_counts
order by cohort desc
;




select distinct riot_id_game_name
from participants
where champion_name = 'Kayle'